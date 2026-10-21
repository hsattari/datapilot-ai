# Fabric / Power BI (guidance + generated semantic model; NOT run against a real tenant)

What exists: `GET /api/v1/export/powerbi` returns `model_bim` (TMSL) and `measures_dax` generated from APPROVED
metrics. Multi-column measures are wrapped so DAX reproduces SQL's NULL-skipping behaviour; verify in Power BI
that Gross Profit on the reference data equals 140.

What does not exist yet (needs your licensed capacity / workspace; verify each step against current Microsoft docs):
1. Fabric workspace on an F-SKU/trial capacity; Lakehouse for gold tables.
2. Getting data in: OneLake shortcut to the ADLS Gen2 raw account, or a Fabric Pipeline / notebook that writes
   `fact_sales` as a Delta table (same schema as `fact_sales` here).
3. Semantic model: either import `model.bim` with Tabular Editor / XMLA and bind the partition, or build the model in
   the service and paste `measures_dax`. Direct Lake mode requires a Lakehouse/Warehouse source and a supported capacity.
4. Governance: Purview/Fabric lineage and workspace roles; row-level security for multi-tenant embedding.
5. Embedding Power BI into the portal (app-owns-data) needs a service principal and an embedded capacity.


## Checking the measures in Power BI Desktop (free)

`GET /api/v1/export/powerbi` needs a login token, so it cannot be opened in a browser. Use the script instead:

1. Start the API (and register, upload `data/synthetic/orders_reference.csv`, approve the model, and approve **Gross Profit** under Business Metrics). Only approved metrics are exported.
2. From the repo root: `python scripts/export_powerbi.py you@example.com` (asks for your password). It writes `powerbi_export/measures.dax`, `model.bim` and `fact_sales.csv`.
3. Power BI Desktop > Get data > Text/CSV > `powerbi_export/fact_sales.csv` > Load. Name the table `fact_sales` (default from the file name).
4. Home > New measure, once per line in `measures.dax` (paste e.g. `Net Revenue = SUM(fact_sales[net_sales])`).
5. Add a Card visual per measure. Expected: Net Revenue 350, Orders 3, Gross Profit 140.
