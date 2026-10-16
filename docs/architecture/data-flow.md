# Data flow (implemented path)

    Browser (Next.js) --/api--> FastAPI --> Postgres (tenants, jobs, metrics, audit)
                                   |--> raw storage (local | ADLS Gen2)  [bronze, immutable, sha256]
    ingestion worker: raw file -> parse -> profile -> bronze table (DuckDB, per tenant)
    approve mapping:  bronze -> TRY_CAST silver/gold -> fact_sales (+ quarantine_sales)
    question -> planner (Azure OpenAI | rules) -> Plan (pydantic) -> approved-metric check
             -> SQL template + bound params -> sqlglot validator -> read-only tenant DB -> answer + provenance

Metadata (operational) is kept separate from analytical data.
