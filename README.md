# DataPilot AI

Multi-tenant analytics SaaS for SMEs: upload data → profile → approved model → governed metrics →
dashboard → validated natural-language answers. Implements the "first deliverable" from the product
proposal (v1.0, 10 Oct 2026).

## Status (what is real today)

| Area | Status |
|---|---|
| Auth (JWT), tenant isolation (server-side tenant resolution), roles | **Complete**, tested |
| Alerts: threshold rules on approved metrics, checked on demand and automatically by the worker (hourly by default); one activity-log entry per state change (triggered / recovered); banner on Home | **Tested locally** (5 API tests + browser). **Not built:** e-mail / Teams notifications (needs a sender account), per-user alert recipients |
| Automatic modelling after upload: clear files are modelled and the built-in metrics (e.g. Gross Profit) approved with no clicks; ambiguous columns, text cost columns and anything failing wait for a person; every automatic step is in the Activity log; admins can switch it off (Settings) | **Tested locally** (9 API tests, 2 browser flows). Not yet tried on real customer files; the detection of unusual column names is rule-based |
| Organisation policy: require two-step sign-in (enforced on every request, no self-lockout, admin reset, SSO accounts exempt) | **Tested locally** (9 API tests, browser flow). SSO exemption relies on the identity provider and is not verified against a real one |
| Plan limits (cost control): per-organisation plan with limits on uploads, rows, questions per day, people and documents; warning at 80%, refusal with a clear message at the limit | **Tested locally** (8 API tests, browser). Limits are placeholders, not validated pricing; no AI token budgets or billing |
| Chat with follow-up questions, answer feedback and CSV export of an answer (Ask Your Data) | **Tested locally** (20 API tests, browser flow `scripts/e2e_chat_ui_test.py`). Follow-ups are read by rules, not by the model; not yet tried by a real user in Azure |
| Upload API (CSV/XLSX/TXT/JSON/JSONL/XML; XML parsed with DOCTYPE/entities refused), validation, immutable raw storage (local; ADLS Gen2 backend coded) | **Complete** locally; **ADLS Gen2 backend verified on real Azure** by the project owner (write, read back, overwrite refused, delete; a real bug in the first version was found and fixed) |
| Async ingestion worker, retries/backoff, profiling, mapping suggestions, quarantine + count reconciliation | **Complete**, tested |
| PostgreSQL metadata schema + Alembic migrations (0001, 0002 incl. pgvector column + HNSW index) | **Verified on real PostgreSQL 16 + pgvector 0.6** (apply, downgrade, re-apply; full suite passes on it; also runs in CI) |
| Metric registry (approval gate, required-input check) | **Complete**, tested |
| Plan → controlled SQL → validator → read-only execution, provenance, caveats | **Complete**, tested |
| Azure OpenAI planner + embeddings | **Live-scored by the project owner on their Azure deployment (`gpt-4.1-mini`): 29/29 on `tests/ai_evaluation/planner_cases.json`** (23 answered by the model, 6 by the rule planner: 5 hard policy/unsupported checks that run before the model on purpose, plus 1 prompt-injection case blocked by Azure's content filter and handled safely). One run of a 29-case set: repeat with `--repeat 3` and grow the set before relying on it. Embeddings `text-embedding-3-large` connectivity verified. Guardrails are tested with a mocked model (`tests/test_planner_llm.py`). Rules are the visible fallback |
| RAG knowledge base (`/api/v1/knowledge/*`): chunking, Azure embeddings (1536-dim), keyword + vector hybrid retrieval, cited answers, tenant isolation | **Complete and tested on SQLite and on PostgreSQL + pgvector** (SQL `<=>` path, HNSW index, tenant-scoped ranking). Tested with the local hashing embeddings, not real Azure embeddings; the Azure-backed `/ask` answer was not exercised end to end |
| Gold-question + security regression suite | **Complete** (66 tests on PostgreSQL; 63 + 3 PG-only skipped on SQLite) |
| Next.js UI (register/login, upload, profile, mapping approval, data-quality panel, schema versions, Business Metrics approval, products/customers approval with a Joins panel, dashboard, Ask Your Data, Reports, Alerts, Activity/Jobs/Usage, Settings) | **Browser-tested end to end** (Chromium/Playwright: `scripts/e2e_ui_test.py`; metric approved in the UI, dashboard 350/140, ask 140, unsafe question refused). Basic styling; people are added by an administrator with a starting password (no e-mailed invitations yet) |
| Saved reports, activity log, jobs, usage, settings (`/reports`, `/activity`, `/jobs`, `/usage`, `/settings`) | **Tested on SQLite and real PostgreSQL** incl. roles (viewer read-only, admin-only activity/settings), tenant isolation, and re-checking metric approval on every report run |
| Sign-in hardening: lockout, TOTP two-step sign-in with recovery codes, user management (add/role/deactivate), rate limits, security headers, production-config guard | **Tested on SQLite and real PostgreSQL, and in the browser** (MFA enrolment, recovery-code sign-in, viewer is read-only). OIDC/Entra token exchange is built and tested against a fake provider only (no Microsoft button in the UI yet, `docs/security/oidc-entra.md`); e-mail invitations are not built; see `docs/security/controls.md` for limits |
| Data quality results and schema versions (`GET /datasets/{id}/quality`, `/schema-versions`) | **Tested on SQLite and real PostgreSQL** (reconciliation, rejection rate, cost coverage, duplicates, negative sales, freshness, drift) |
| Products/customers dimensions with joins (`approve` with `role`, `GET /relationships`, "by category / by segment" questions) | **Tested on SQLite, real PostgreSQL, dbt and in the browser.** Joins are `LEFT JOIN` on unique keys (duplicates quarantined), unmatched sales show as "Unassigned" and totals always reconcile. Databricks version is mock-tested only; inventory template not built |
| dbt Core retail template | **`dbt build` verified** (dbt 1.12 + DuckDB: 8/8 pass, gross profit 140, bad row quarantined, duplicate fails the unique test) via `dbt/check_template.py`, also in CI. Other warehouses (Databricks/Fabric) not tested |
| CI workflow | **Running on GitHub, all 4 jobs green** (api, web, PostgreSQL+pgvector, dbt) |
| Data lifecycle: organisation export (ZIP + manifest, formula-safe CSV), organisation deletion with step-up confirmation, person erasure, retention purge (`/export/tenant`, `/tenant/delete`, `DELETE /users/{id}`) | **Tested on SQLite and real PostgreSQL and in the browser** (all tenant-scoped tables derived from the schema must be empty after deletion; other tenants untouched; failed external delete leaves data intact for retry). **Not verified:** ADLS and Databricks deletion paths against real services; Azure backups are outside this control. See `docs/security/data-lifecycle.md` |
| Observability: request IDs, structured JSON logs, OpenTelemetry spans (requests, queries, ingestion) exported to Application Insights when configured | **Tested locally** with an in-memory exporter, including privacy tests (no question text, e-mail, tokens, file names or query strings in logs/spans). **Not tested against a real Application Insights resource** |
| Malware scanning of uploads (ClamAV clamd, fail-closed; production refuses to start without it) | **Tested against a fake clamd** (protocol framing, chunking, errors, timeouts, fail-open audit; tests mutation-checked). **Not tested against a real ClamAV daemon.** Compose adds a `clamav` container (not run here) |
| Docker Compose, Terraform | **Terraform: `init`, `validate`, `plan` and `apply` verified by the project owner** (Terraform 1.16.6, azurerm 4.81.0). On the real resources: PostgreSQL 16 over TLS, pgvector allow-list and a vector query, ADLS Gen2 storage checks (`scripts/test_azure_resources.py`) and `alembic upgrade head` (revision 0007, 19 tables, `vector` enabled; `scripts/check_db.py`) all passed. Hosting (`hosting.tf`, opt-in) was applied by the owner: the web app opens at its Azure address and works; API, worker and ClamAV run as internal Container Apps with secrets in Key Vault. **Not yet verified in Azure:** upload through ClamAV, the worker's ADLS ingestion, Application Insights. Still missing: private networking, custom domain/WAF, Entra sign-in (`docs/operations/azure-starter-deploy.md`). **Docker Compose: not run** (no Docker daemon available) |
| MCP server (`apps/mcp`): 12 read-only tools over the REST API (tenant scoping/validation reused; no write, raw-SQL or tenant tools; preview shows SQL without executing; ids validated) | **Complete**; tested in-process (incl. tenant scoping and path-escape tests). Not tested with a specific client app |
| Analytics backend abstraction + **Databricks SQL / Unity Catalog adapter** (schema per tenant, read-only token for queries, named-parameter binding, PySpark ingest template) | **Written, mock-tested only — UNTESTED against a real workspace.** See `connectors/databricks/README.md` |
| **Power BI semantic model export** (`GET /api/v1/export/powerbi`: TMSL + DAX from approved metrics, SQL-equivalent NULL handling) | **Verified in Power BI Desktop by the project owner:** Net Revenue 350, Orders 3, Gross Profit 140 from the generated DAX on the reference data (`scripts/demo_setup.py` produces the files). The partition is still a template you bind to your source; dimension tables are not exported yet. See `docs/architecture/fabric-powerbi.md` |
| Fabric Lakehouse/OneLake/Pipelines/Dataflow Gen2/Direct Lake, Azure AI Search backend, Entra ID auth | **Not implemented** (need your licensed capacity/workspace); guidance in `docs/architecture/fabric-powerbi.md` |

**Proposal coverage, section by section: `docs/proposal-coverage.md`.**

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
- Single tenant per user. Malware scanning of uploads and a data export/deletion workflow are not built yet; OIDC/Entra is untested against a real identity provider.
- Rule-based planner is keyword-based and intentionally conservative.
- Logs store question text and generated SQL in `analytical_queries`; set retention before launch and complete a DPIA.
