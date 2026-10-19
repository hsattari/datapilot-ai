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
| PostgreSQL metadata schema + Alembic migration | **Complete** (migration verified on SQLite; run it against PostgreSQL before relying on it) |
| Metric registry (approval gate, required-input check) | **Complete**, tested |
| Plan → controlled SQL → validator → read-only execution, provenance, caveats | **Complete**, tested |
| Azure OpenAI planner + embeddings | **Verified by you** against your Azure resource (`scripts/test_azure_openai.py`: chat `gpt-4.1-mini` OK, embeddings `text-embedding-3-large` OK); the planner's JSON-plan call path itself has not been exercised end to end. Deterministic rule-based planner is the fallback |
| RAG knowledge base (`/api/v1/knowledge/*`): chunking, Azure embeddings (1536-dim), keyword + vector hybrid retrieval, cited answers, tenant isolation | **Complete and tested on SQLite** (portable cosine search). The **PostgreSQL + pgvector path (migration 0002, HNSW index, `<=>` query) is written but UNTESTED** — pgvector was not available in the build sandbox; run it against a real server before relying on it |
| Gold-question + security regression suite | **Complete** (44 tests) |
| Next.js UI (register/login, upload, profile, mapping approval, dashboard, Ask Your Data) | **Builds and typechecks**; not browser-tested |
| dbt Core retail template | **Written; `dbt build` not run** (SQL logic checked on DuckDB against the reference data) |
| Docker Compose, Terraform, CI workflow | **Written, not executed** (no Docker daemon / Terraform in the build sandbox) |
| Databricks/PySpark/Delta/Unity Catalog, Fabric/OneLake/Power BI, Azure AI Search backend, MCP, Dataflow Gen2 | **Not implemented** — see `connectors/*/README.md`. They need external credentials/licences and are roadmap phases 5–7. |

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
