"""Per-tenant analytical store (Starter profile: DuckDB, one database file per tenant).

Tenant isolation: separate file per tenant, read-only connections for queries.
The Databricks/Fabric profiles implement the same interface (see connectors/).
"""
import re
from pathlib import Path

import duckdb
import pandas as pd

from ..config import get_settings
from .dimensions import DIMS

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _path(tenant_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", tenant_id):
        raise ValueError("Invalid tenant id")
    root = Path(get_settings().analytics_path)
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{tenant_id}.duckdb"


def writer(tenant_id: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(_path(tenant_id)))


def reader(tenant_id: str) -> duckdb.DuckDBPyConnection:
    p = _path(tenant_id)
    if not p.exists():
        con = duckdb.connect(str(p))
        con.close()
    return duckdb.connect(str(p), read_only=True)


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return name


def load_bronze(tenant_id: str, table: str, df: pd.DataFrame) -> None:
    table = ident(table)
    con = writer(tenant_id)
    try:
        con.register("df_in", df)
        con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM df_in')
    finally:
        con.close()


def build_fact_sales(tenant_id: str, bronze_table: str, mapping: dict[str, str]) -> dict:
    """Silver/Gold step: cast mapped columns with safe TRY_CAST; reject bad rows to quarantine."""
    bronze_table = ident(bronze_table)
    for col in mapping.values():
        ident(col)
    required = ["order_id", "order_date", "net_sales"]
    missing = [r for r in required if r not in mapping]
    if missing:
        raise ValueError(f"Missing required mappings: {missing}")

    def col(canon: str, cast: str, default: str = "NULL") -> str:
        if canon not in mapping:
            return f"{default} AS {canon}"
        return f'TRY_CAST(NULLIF(TRIM("{mapping[canon]}"), \'\') AS {cast}) AS {canon}'

    def txt(canon: str) -> str:
        return (f'NULLIF(TRIM("{mapping[canon]}"), \'\') AS {canon}' if canon in mapping
                else f"NULL AS {canon}")

    select = ", ".join([
        txt("order_id"), col("order_date", "DATE"), col("net_sales", "DECIMAL(18,2)"),
        col("cost_of_goods_sold", "DECIMAL(18,2)"), txt("product"), txt("region"), txt("customer"),
    ])
    con = writer(tenant_id)
    try:
        con.execute(f'CREATE OR REPLACE TEMP TABLE stg AS SELECT {select} FROM "{bronze_table}"')
        # Quarantine: unparseable key, date or sales amount (negative amounts are valid refunds).
        con.execute("CREATE OR REPLACE TABLE quarantine_sales AS SELECT * FROM stg "
                    "WHERE order_id IS NULL OR order_date IS NULL OR net_sales IS NULL")
        con.execute("CREATE OR REPLACE TABLE fact_sales AS SELECT * FROM stg "
                    "WHERE order_id IS NOT NULL AND order_date IS NOT NULL AND net_sales IS NOT NULL")
        loaded = con.execute("SELECT count(*) FROM fact_sales").fetchone()[0]
        rejected = con.execute("SELECT count(*) FROM quarantine_sales").fetchone()[0]
        total = con.execute("SELECT count(*) FROM stg").fetchone()[0]
        cost_missing = con.execute(
            "SELECT count(*) FROM fact_sales WHERE cost_of_goods_sold IS NULL").fetchone()[0]
        assert loaded + rejected == total  # reconcile counts: no silent record loss
    finally:
        con.close()
    return {"rows_loaded": loaded, "rows_rejected": rejected, "cost_missing_rows": cost_missing,
            "has_cost": "cost_of_goods_sold" in mapping}


def build_dimension(tenant_id: str, bronze_table: str, role: str, mapping: dict[str, str]) -> dict:
    """Build dim_product / dim_customer: unique non-blank key; duplicates and blank keys go to quarantine."""
    bronze_table = ident(bronze_table)
    d = DIMS[role]
    kc, ac = d["key_canon"], d["attr_canon"]
    if kc not in mapping or ac not in mapping:
        raise ValueError(f"Missing required mappings: {[c for c in (kc, ac) if c not in mapping]}")
    src_k, src_a = ident(mapping[kc]), ident(mapping[ac])
    table, k, a = d["table"], d["key_col"], d["attr_col"]
    con = writer(tenant_id)
    try:
        con.execute(f'CREATE OR REPLACE TEMP TABLE dstg AS SELECT NULLIF(TRIM("{src_k}"), \'\') AS {k}, '
                    f'NULLIF(TRIM("{src_a}"), \'\') AS {a} FROM "{bronze_table}"')
        con.execute(f"CREATE OR REPLACE TEMP TABLE dranked AS SELECT *, row_number() OVER "
                    f"(PARTITION BY {k} ORDER BY {a} NULLS LAST) AS rn FROM dstg WHERE {k} IS NOT NULL")
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT {k}, {a} FROM dranked WHERE rn = 1")
        con.execute(f"CREATE OR REPLACE TABLE quarantine_{table} AS SELECT {k}, {a} FROM dstg WHERE {k} IS NULL "
                    f"UNION ALL SELECT {k}, {a} FROM dranked WHERE rn > 1")
        loaded = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        rejected = con.execute(f"SELECT count(*) FROM quarantine_{table}").fetchone()[0]
        total = con.execute("SELECT count(*) FROM dstg").fetchone()[0]
        no_attr = con.execute(f"SELECT count(*) FROM {table} WHERE {a} IS NULL").fetchone()[0]
        assert loaded + rejected == total
    finally:
        con.close()
    return {"rows_loaded": loaded, "rows_rejected": rejected, "attribute_missing_rows": no_attr, "role": role}


def list_tables(tenant_id: str) -> set[str]:
    con = reader(tenant_id)
    try:
        return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        con.close()


def export_table(tenant_id: str, table: str) -> tuple[list[str], list[tuple]]:
    table = ident(table)
    con = reader(tenant_id)
    try:
        cur = con.execute(f'SELECT * FROM "{table}"')
        return [d[0] for d in cur.description], cur.fetchall()
    finally:
        con.close()


def drop_tenant(tenant_id: str) -> int:
    """Delete this tenant's database file (and its write-ahead log). Returns files removed."""
    p = _path(tenant_id)
    n = 0
    for f in (p, p.with_name(p.name + ".wal")):
        if f.exists():
            f.unlink()
            n += 1
    return n
