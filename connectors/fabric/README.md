# Databricks / Fabric profiles — NOT IMPLEMENTED YET

The Starter profile (DuckDB) is the only working execution engine. Databricks (PySpark, Delta Lake,
Unity Catalog) and Fabric (Lakehouse, Pipelines, Warehouse, Power BI) are planned as adapters behind the
same interface used by `apps/api/app/services/analytics_store.py` (`load_bronze`, `build_fact_sales`,
`reader`). They require external credentials, capacity and licences; verify against current Microsoft/
Databricks documentation before implementing (roadmap phases 6–7).
