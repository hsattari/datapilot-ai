"""Analytical backends. DuckDB (Starter, default) and Databricks SQL / Unity Catalog (Scale).

Both implement the same interface, so ingestion, modelling and the governed query path are engine-agnostic.
Tenant isolation: DuckDB = one database file per tenant; Databricks = one schema per tenant
(catalog.t_<tenant>), with the query connection pinned to that schema and bare table names only.
"""
import re
import threading
from functools import lru_cache
from typing import Any, Protocol

import pandas as pd

from ..config import get_settings
from . import analytics_store as duck

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_TENANT = re.compile(r"^[0-9a-f-]{36}$")


class Backend(Protocol):
    name: str
    dialect: str  # sqlglot dialect used to validate generated SQL

    def month_expr(self, col: str) -> str: ...
    def load_bronze(self, tenant_id: str, table: str, df: pd.DataFrame) -> None: ...
    def build_fact_sales(self, tenant_id: str, bronze_table: str, mapping: dict[str, str]) -> dict: ...
    def query(self, tenant_id: str, sql: str, params: list) -> tuple[list[str], list[list]]: ...
    def scalar(self, tenant_id: str, sql: str) -> Any: ...


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return name


class DuckDBBackend:
    name, dialect = "duckdb", "duckdb"

    def month_expr(self, col: str) -> str:
        return f"strftime({col}, '%Y-%m')"

    def load_bronze(self, tenant_id, table, df):
        duck.load_bronze(tenant_id, table, df)

    def build_fact_sales(self, tenant_id, bronze_table, mapping):
        return duck.build_fact_sales(tenant_id, bronze_table, mapping)

    def query(self, tenant_id, sql, params):
        s = get_settings()
        con = duck.reader(tenant_id)  # read-only connection scoped to this tenant's file
        timer = threading.Timer(s.query_timeout_seconds, con.interrupt)
        timer.start()
        try:
            cur = con.execute(sql, params)
            return [d[0] for d in cur.description], [list(r) for r in cur.fetchmany(s.query_row_limit)]
        finally:
            timer.cancel()
            con.close()

    def scalar(self, tenant_id, sql):
        con = duck.reader(tenant_id)
        try:
            return con.execute(sql).fetchone()[0]
        finally:
            con.close()


class DatabricksBackend:
    """Databricks SQL warehouse + Unity Catalog. UNTESTED against a real workspace (mock-tested only).

    Recommended setup: a dedicated catalog; a service principal for writes (ingestion) and a SEPARATE
    principal with only USE CATALOG/USE SCHEMA/SELECT for the query path (DATABRICKS_READONLY_TOKEN).
    """
    name, dialect = "databricks", "databricks"

    def __init__(self):
        s = get_settings()
        if not (s.databricks_server_hostname and s.databricks_http_path and s.databricks_token
                and s.databricks_catalog):
            raise RuntimeError("Databricks backend needs DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, "
                               "DATABRICKS_TOKEN and DATABRICKS_CATALOG")
        self.s = s

    def month_expr(self, col: str) -> str:
        return f"date_format({col}, 'yyyy-MM')"

    def schema(self, tenant_id: str) -> str:
        if not _TENANT.match(tenant_id):
            raise ValueError("Invalid tenant id")
        return "t_" + tenant_id.replace("-", "")

    def _connect(self, tenant_id: str, write: bool):
        from databricks import sql  # imported lazily so DuckDB-only installs don't need it

        token = (self.s.databricks_token if write else (self.s.databricks_readonly_token or self.s.databricks_token))
        return sql.connect(server_hostname=self.s.databricks_server_hostname, http_path=self.s.databricks_http_path,
                           access_token=token, catalog=self.s.databricks_catalog,
                           # Writers connect to the catalog only (the tenant schema may not exist yet) and then
                           # USE SCHEMA; readers are pinned to the tenant schema at connection time.
                           **({} if write else {"schema": self.schema(tenant_id)}),
                           session_configuration={"STATEMENT_TIMEOUT": str(self.s.query_timeout_seconds)}
                           if not write else None)

    @staticmethod
    def to_named(sql: str, params: list) -> tuple[str, dict]:
        """Convert positional ? placeholders to named :p0.. parameters (native, injection-safe)."""
        out, i = [], 0
        for part in re.split(r"(\?)", sql):
            if part == "?":
                out.append(f":p{i}")
                i += 1
            else:
                out.append(part)
        if i != len(params):
            raise ValueError("Parameter count mismatch")
        return "".join(out), {f"p{k}": v for k, v in enumerate(params)}

    def _exec(self, cur, sql: str, params: dict | None = None):
        cur.execute(sql, params) if params else cur.execute(sql)

    def load_bronze(self, tenant_id, table, df):
        table = _ident(table)
        cols = [_ident(c) for c in df.columns]
        schema = self.schema(tenant_id)
        # Small/medium loads via batched native-parameter INSERTs. For large files use the PySpark / COPY INTO
        # template in connectors/databricks/ instead.
        batch = max(1, 200 // max(1, len(cols)))
        with self._connect(tenant_id, True) as conn, conn.cursor() as cur:
            self._exec(cur, f"CREATE SCHEMA IF NOT EXISTS {schema}")
            self._exec(cur, f"USE SCHEMA {schema}")
            col_defs = ", ".join(f"`{c}` STRING" for c in cols)
            self._exec(cur, f"CREATE OR REPLACE TABLE {table} ({col_defs})")
            rows = df.astype(str).values.tolist()
            for i in range(0, len(rows), batch):
                chunk = rows[i:i + batch]
                ph = ", ".join("(" + ", ".join(f":p{r}_{c}" for c in range(len(cols))) + ")"
                               for r in range(len(chunk)))
                params = {f"p{r}_{c}": v for r, row in enumerate(chunk) for c, v in enumerate(row)}
                self._exec(cur, f"INSERT INTO {table} VALUES {ph}", params)

    def build_fact_sales(self, tenant_id, bronze_table, mapping):
        bronze_table = _ident(bronze_table)
        for col in mapping.values():
            _ident(col)
        missing = [r for r in ("order_id", "order_date", "net_sales") if r not in mapping]
        if missing:
            raise ValueError(f"Missing required mappings: {missing}")

        def cast(canon, typ):
            return (f"TRY_CAST(NULLIF(TRIM(`{mapping[canon]}`), '') AS {typ}) AS {canon}" if canon in mapping
                    else f"CAST(NULL AS {typ}) AS {canon}")

        def txt(canon):
            return (f"NULLIF(TRIM(`{mapping[canon]}`), '') AS {canon}" if canon in mapping
                    else f"CAST(NULL AS STRING) AS {canon}")

        select = ", ".join([txt("order_id"), cast("order_date", "DATE"), cast("net_sales", "DECIMAL(18,2)"),
                            cast("cost_of_goods_sold", "DECIMAL(18,2)"), txt("product"), txt("region"),
                            txt("customer")])
        with self._connect(tenant_id, True) as conn, conn.cursor() as cur:
            self._exec(cur, f"USE SCHEMA {self.schema(tenant_id)}")
            self._exec(cur, f"CREATE OR REPLACE TEMPORARY VIEW stg AS SELECT {select} FROM {bronze_table}")
            self._exec(cur, "CREATE OR REPLACE TABLE quarantine_sales AS SELECT * FROM stg "
                            "WHERE order_id IS NULL OR order_date IS NULL OR net_sales IS NULL")
            self._exec(cur, "CREATE OR REPLACE TABLE fact_sales AS SELECT * FROM stg "
                            "WHERE order_id IS NOT NULL AND order_date IS NOT NULL AND net_sales IS NOT NULL")

            def one(q):
                self._exec(cur, q)
                return cur.fetchone()[0]

            loaded, rejected = one("SELECT count(*) FROM fact_sales"), one("SELECT count(*) FROM quarantine_sales")
            total = one("SELECT count(*) FROM stg")
            cost_missing = one("SELECT count(*) FROM fact_sales WHERE cost_of_goods_sold IS NULL")
            assert loaded + rejected == total  # reconcile: no silent record loss
        return {"rows_loaded": loaded, "rows_rejected": rejected, "cost_missing_rows": cost_missing,
                "has_cost": "cost_of_goods_sold" in mapping}

    def query(self, tenant_id, sql, params):
        named_sql, named = self.to_named(sql, params)
        with self._connect(tenant_id, False) as conn, conn.cursor() as cur:
            self._exec(cur, named_sql, named)
            cols = [d[0] for d in cur.description]
            return cols, [list(r) for r in cur.fetchmany(self.s.query_row_limit)]

    def scalar(self, tenant_id, sql):
        with self._connect(tenant_id, False) as conn, conn.cursor() as cur:
            self._exec(cur, sql)
            return cur.fetchone()[0]


@lru_cache
def _make(name: str) -> Backend:
    return DatabricksBackend() if name == "databricks" else DuckDBBackend()


def get_backend() -> Backend:
    return _make(get_settings().analytics_backend)
