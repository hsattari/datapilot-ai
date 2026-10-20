# Databricks (Scale profile) — adapter written, UNTESTED against a real workspace

`apps/api/app/services/backends.py::DatabricksBackend` runs the same ingestion/modelling/governed-query path on a
Databricks SQL warehouse with Unity Catalog. Verified only by mock tests (SQL generation, parameter binding, tenant
schema pinning). Validate it in a dev workspace before relying on it.

## Enable

    ANALYTICS_BACKEND=databricks
    DATABRICKS_SERVER_HOSTNAME=adb-xxxx.azuredatabricks.net     # not secret
    DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxx               # not secret
    DATABRICKS_CATALOG=datapilot
    DATABRICKS_TOKEN=...             # write principal — keep in .env / Key Vault only
    DATABRICKS_READONLY_TOKEN=...    # SELECT-only principal for the query path (strongly recommended)

Isolation: one schema per tenant (`t_<tenant-id>`); query connections are pinned to that schema and the SQL
validator only allows the bare `fact_sales` table (3-part names are rejected).

## Unity Catalog setup (run as a metastore/catalog admin; adjust names)

    CREATE CATALOG IF NOT EXISTS datapilot;
    -- service principals created in your workspace/Entra ID:
    GRANT USE CATALOG, CREATE SCHEMA ON CATALOG datapilot TO `datapilot-writer`;
    GRANT USE CATALOG ON CATALOG datapilot TO `datapilot-reader`;
    -- the reader needs USE SCHEMA + SELECT on each tenant schema. Grant at catalog level only if all tenants may be
    -- visible to that principal (the app still pins one schema per connection); for stronger isolation, create a
    -- reader per tenant or grant per schema when the tenant is created.
    GRANT USE SCHEMA, SELECT ON CATALOG datapilot TO `datapilot-reader`;

## Large loads (PySpark / Delta)

`load_bronze` uses batched parameterised INSERTs — fine for small/medium files only. For large files use
`pyspark_ingest.py` (a Databricks job: raw file in ADLS/Volume -> Delta bronze table in the tenant schema), then call
the same `build_fact_sales` step. The script is a template, not run here.
