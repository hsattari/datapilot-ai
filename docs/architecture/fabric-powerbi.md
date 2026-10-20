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
