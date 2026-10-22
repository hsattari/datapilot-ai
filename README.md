# DataPilot AI

Multi-tenant analytics SaaS for SMEs: upload data → profile → approved model → governed metrics →
dashboard → validated natural-language answers. Implements the "first deliverable" from the product
proposal (v1.0, 10 Oct 2026).

## Status (what is real today)

| Area | Status |
|---|---|
| Auth (JWT), tenant isolation (server-side tenant resolution), roles | **Complete**, tested |
| Upload API (CSV/XLSX/TXT/JSON/JSONL), validation, immutable raw storage (local; ADLS Gen2 backend coded) | **Complete** locally; ADLS backend **untested** (needs Azure credentials) |
| Async ingestion worker, retries/backoff, profiling, mapping suggestions, quarantine + count reconciliation | **Complete**, tested |
| PostgreSQL metadata schema + Alembic migrations (0001, 0002 incl. pgvector column + HNSW index) | **Verified on real PostgreSQL 16 + pgvector 0.6** (apply, downgrade, re-apply; full suite passes on it; also runs in CI) |
| Metric registry (approval gate, required-input check) | **Complete**, tested |
| Plan → controlled SQL → validator → read-only execution, provenance, caveats | **Complete**, tested |
| Azure OpenAI planner + embeddings | **Verified by you** against your Azure resource (`scripts/test_azure_openai.py`: chat `gpt-4.1-mini` OK, embeddings `text-embedding-3-large` OK); the planner's JSON-plan call path itself has not been exercised end to end. Deterministic rule-based planner is the fallback |
| RAG knowledge base (`/api/v1/knowledge/*`): chunking, Azure embeddings (1536-dim), keyword + vector hybrid retrieval, cited answers, tenant isolation | **Complete and tested on SQLite and on PostgreSQL + pgvector** (SQL `<=>` path, HNSW index, tenant-scoped ranking). Tested with the local hashing embeddings, not real Azure embeddings; the Azure-backed `/ask` answer was not exercised end to end |
| Gold-question + security regression suite | **Complete** (66 tests on PostgreSQL; 63 + 3 PG-only skipped on SQLite) |
| Next.js UI (register/login, upload, profile, mapping approval, data-quality panel, schema versions, Business Metrics approval, products/customers approval with a Joins panel, dashboard, Ask Your Data) | **Browser-tested end to end** (Chromium/Playwright: `scripts/e2e_ui_test.py`; metric approved in the UI, dashboard 350/140, ask 140, unsafe question refused). Basic styling; Reports, Alerts, Activity, Settings screens not built yet |
| Data quality results and schema versions (`GET /datasets/{id}/quality`, `/schema-versions`) | **Tested on SQLite and real PostgreSQL** (reconciliation, rejection rate, cost coverage, duplicates, negative sales, freshness, drift) |
| Products/customers dimensions with joins (`approve` with `role`, `GET /relationships`, "by category / by segment" questions) | **Tested on SQLite, real PostgreSQL, dbt and in the browser.** Joins are `LEFT JOIN` on unique keys (duplicates quarantined), unmatched sales show as "Unassigned" and totals always reconcile. Databricks version is mock-tested only; inventory template not built |
| dbt Core retail template | **`dbt build` verified** (dbt 1.12 + DuckDB: 8/8 pass, gross profit 140, bad row quarantined, duplicate fails the unique test) via `dbt/check_template.py`, also in CI. Other warehouses (Databricks/Fabric) not tested |
| CI workflow | **Running on GitHub, all 4 jobs green** (api, web, PostgreSQL+pgvector, dbt) |
| Docker Compose, Terraform | **Written, not executed** (no Docker daemon / Terraform in the build sandbox) |
| MCP server (`apps/mcp`): 6 read-only tools over the REST API (tenant scoping/validation reused; no write, raw-SQL or tenant tools) | **Complete**; tested in-process and via a real stdio handshake. Not tested with a specific client app |
| Analytics backend abstraction + **Databricks SQL / Unity Catalog adapter** (schema per tenant, read-only token for queries, named-parameter binding, PySpark ingest template) | **Written, mock-tested only — UNTESTED against a real workspace.** See `connectors/databricks/README.md` |
| **Power BI semantic model export** (`GET /api/v1/export/powerbi`: TMSL + DAX from approved metrics, SQL-equivalent NULL handling) | **Generated, not executed in Power BI.** The partition is a template you bind to your source. See `docs/architecture/fabric-powerbi.md` |
| Fabric Lakehouse/OneLake/Pipelines/Dataflow Gen2/Direct Lake, Azure AI Search backend, Entra ID auth | **Not implemented** (need your licensed capacity/workspace); guidance in `docs/architecture/fabric-powerbi.md` |

The Starter profile uses DuckDB (one database file per tenant) as the analytical engine. Verify all
Azure/dbt/Databricks/Fabric versions and APIs against current official documentation before production.

## Quick start (no Docker)

    pip install -r apps/api/requirements.txt
    cp .env.example .env     # for local dev set DATABASE_URL=sqlite:///./dev.db, local paths
    cd apps/api && alembic upgrade head
    uvicorn app.main:app --reload          # API on :8000
    python -m app.worker                   # ingestion worker (separate terminal)
    cd ../web && npm install && npm run dev   # UI on :3000

With Docker: `cp .env.example .env && docker compose up --build`.

## Demo flow

1. Generate/see data: `data/synthetic/` (`orders_reference.csv` has the brief's A/B/C orders, `orders_retail.csv` has 800 orders).
2. Register, upload `orders_reference.csv`, approve the proposed mapping.
3. Approve the `gross_profit` metric (admin) and ask "What is our gross profit?" → **140**.

## Knowledge base (RAG)

    POST /api/v1/knowledge/documents          {"title","text"}        (admin/analyst)
    POST /api/v1/knowledge/documents/upload   .txt/.md file           (admin/analyst)
    POST /api/v1/knowledge/search             {"query","k"}           (any member)
    POST /api/v1/knowledge/ask                {"query"}  -> cited answer or excerpts

Documents are untrusted text: they are never executed as instructions, SQL or metrics, and numbers must still
come from the governed metric path. Without Azure configured, a lexical hashing fallback is used (not semantic;
recorded per document as `embedding_mode`). `EMBEDDING_DIMENSIONS` defaults to 1536 (shortened
`text-embedding-3-large` vectors, compatible with pgvector indexes); changing it requires re-indexing and a new migration.

## Tests

    cd apps/api && python -m pytest -q

Covers the reference calculation (net 350, cost 210, gross profit 140), cross-tenant access, spoofed tenant
header, upload validation, quarantine, unsafe SQL/prompt-injection refusals, and gold questions
(`tests/ai_evaluation/gold_questions.json`).

## Key design rules enforced in code

- Tenant comes from the authenticated membership, never from the browser.
- The model proposes a plan only; SQL is built from templates with bound parameters, then re-validated
  (single SELECT, allowlisted table/functions, LIMIT required) and run on a read-only, per-tenant connection.
- Metrics are inactive until approved; a metric whose inputs are unmapped cannot be approved.
- Bad rows are quarantined and loaded + rejected must equal staged (no silent loss).
- Prefer a correct refusal/clarification over a fabricated answer.

## Known limitations / next steps

- Only one sales dataset feeds `fact_sales` (latest approved replaces the previous one); multi-table joins
  (products, customers, inventory) are next.
- Single tenant per user; JWT only (add OIDC/MFA). No rate limiting, malware scanning, or export endpoint yet.
- Rule-based planner is keyword-based and intentionally conservative.
- Logs store question text and generated SQL in `analytical_queries`; set retention before launch and complete a DPIA.
