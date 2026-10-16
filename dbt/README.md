# dbt templates

`templates/retail` is a controlled template (orders -> staging -> fact_sales). Projects are generated
from templates only; arbitrary user-supplied Jinja or SQL is never accepted.

Run (requires `pip install dbt-duckdb`; validate against your dbt version/adapter):

    cd dbt/templates/retail && cp profiles.yml.example profiles.yml
    dbt build --profiles-dir .

The API's Starter profile performs the same silver/gold logic in
`apps/api/app/services/analytics_store.py`; dbt is the path for Databricks/Fabric targets.
