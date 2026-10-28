# Proposal coverage

Checked against *DataPilot AI Comprehensive Product & Technical Proposal v1.0* (10 October 2026), section by section,
on 27 October 2026 from the code, tests and what the project owner has run in Azure.

Status words, used strictly:

- **Verified**: run by the project owner on the real service (Azure, Databricks, Power BI Desktop).
- **Tested**: automated tests pass locally (SQLite, and real PostgreSQL in CI), no real external service involved.
- **Partial**: some of the requirement exists; what is missing is listed.
- **Not built**: nothing exists. Where it cannot be built without something only the owner has, that is said.

## 3. Modules

| Module | Status | Detail |
|---|---|---|
| Onboarding and workspaces | Partial | Registration, organisation name, currency, time zone, users, roles: **Tested**. Not built: business description, consent capture, subscription limits, separate workspaces inside one organisation |
| Source management | Partial | CSV, XLSX, TXT, JSON, JSONL: **Tested** locally. In Azure the web app opens and works (owner), the storage layer was checked directly, but an upload through ClamAV and the worker has **not yet been confirmed** since the Azure Files fix. XML upload: **Tested** (hardened parser, see below). Not built: API connectors, database connectors, `POST /data-sources/{id}/sync`. Schema history: **Tested** |
| Automated data engineering | Partial | Schema inference, profiling, mapping suggestions with confidence, quarantine, reconciled counts, retries with backoff, quality checks: **Tested**. Sales files accumulate with upsert by order ID (latest file wins, reported): **Tested**. Not built: lineage view, watermarks / API cursors / CDC, snapshots |
| Business intelligence | Partial | Home dashboard tiles, monthly trend, automatic insights and anomaly flag, "why did it change" driver analysis, saved reports: **Tested**. Not built: industry templates other than retail, a separate Dashboards page with saved dashboards, scheduled report delivery, weekly management summary |
| Conversational analytics | Tested | Structured plan, governed metrics, safe SQL, provenance, caveats, refusals: **Tested**; planner **Verified** live 29/29. **Conversations** with follow-ups ("and by region?", "same for gross profit", "what about the last 3 months"): read by rules as a change to the previous plan, shown back as "Understood as: ...", same validation and refusals as any question; private to their owner. Answer **feedback** (helpful / not helpful with a reason; administrators see it on Activity). **CSV export** of an answer (administrators and analysts only, re-runs the plan so approval is re-checked, formula-safe, rate-limited, logged). Chats are in the organisation export and erased with the person, the organisation and by retention. Follow-ups never go to the Azure OpenAI planner (rules only, by design); the 29 live planner cases are single questions and do not exercise them, so follow-up quality is covered by the API and browser tests only |
| Business recommendations | Partial | Evidence-based driver analysis and anomaly flags with caveats. Not built: written recommendations with baselines, confidence and next steps |
| RAG knowledge assistant | Tested | Hybrid keyword + vector retrieval, cited answers, tenant isolation: tested on PostgreSQL + pgvector (**Verified** vector queries on Azure PostgreSQL). Azure AI Search backend not built (pgvector is allowed by the proposal) |
| Monitoring and alerts | Partial | Threshold alerts checked by the worker, one log entry per state change, Home banner: **Tested**. Not built: e-mail/Teams delivery (needs a sender account), alerts for missing files, refresh failures and schema changes as their own rules, weekly summary |

## 4-7. Architecture, ingestion, medallion, dbt

| Item | Status | Detail |
|---|---|---|
| Next.js + FastAPI + PostgreSQL + ADLS Gen2 | **Verified** | Running in Azure Container Apps; ADLS and PostgreSQL checked by `scripts/test_azure_resources.py` |
| Starter profile (DuckDB) | Tested | One file per tenant on an Azure Files share in Azure. SMB has no byte-range locks; worker and API open the file one after the other. Not load-tested |
| dbt Core retail template | **Verified** | `dbt build` 8/8 pass (dbt 1.12 + DuckDB), also in CI. Other warehouses not tested. Not built: snapshots, exposures |
| File validation, size/parser limits, checksum | Tested | XML: DOCTYPE and entity declarations are refused before parsing and again by the parser (XXE and entity-expansion attacks tested); depth, element and column caps |
| Malware scanning of uploads | Tested | Against a **fake** ClamAV only; a real daemon has not been tried |
| Bronze / Silver / Gold | Tested | |
| Databricks adapter, Unity Catalog, PySpark template | Partial | Connection **Verified** live ("ALL Passed"). Schema-per-tenant build, dimension build and accumulate path: mock-tested only |
| Fabric (Pipelines, Dataflow Gen2, Lakehouse, Direct Lake) | Not built | Needs the owner's licensed Fabric capacity. Guidance in `docs/architecture/fabric-powerbi.md` |

## 8. Semantic layer

| Item | Status | Detail |
|---|---|---|
| Metric registry with approval gate, required-input check, version | Tested | Built-in metrics are now approved automatically when their columns exist (admin can switch off); custom metrics always need an administrator |
| Owner, unit, default filters, effective date, example questions, `metric_dimensions` | Partial | Name, description, expression, aggregation, status, version, allowed dimensions exist. The rest is not stored |

## 9. Power BI

| Item | Status | Detail |
|---|---|---|
| Generated model + DAX | **Verified** | 350 / 3 / 140 in Power BI Desktop. Not built: dimension tables, date table with time intelligence measures (YTD, prior year), RLS/OLS, deployment pipelines |

## 10-13. AI, RAG, MCP, evaluation

| Item | Status | Detail |
|---|---|---|
| Ten-step request lifecycle | Tested | Server-side tenant, hard refusals before the model, plan validated, SQL validated (single SELECT, allowlist, LIMIT), read-only execution, provenance, audit |
| Gold-question set and gates | Partial | 29 planner cases (**Verified** live) plus a security regression suite. Not built: stored evaluation results per release (`ai_evaluations`), latency and cost per question, repeated runs |
| MCP tools | Tested | 12 read-only tools cover all 8 the proposal names (`ask_data` is its `run_approved_query`). Not tested with a specific MCP client app |
| Copilot readiness | Not built | Needs Fabric/Power BI licensing |

## 14. Metadata database and API

Present: tenants, users, memberships, identities, data_sources, source_files, ingestion_jobs, schema_versions, datasets, quality results, metrics, reports, alerts, analytical_queries, audit_events, documents, chunks, deletion log.

Not built: `workspaces`, `data_models`, `model_dependencies`, `data_quality_rules` (rules are fixed in code), `metric_dimensions`, `dashboards`, `ai_evaluations`. Added: `conversations`, `conversation_messages` (with feedback).

API gaps against the illustrative list: `POST /data-sources/{id}/sync`. `POST /conversations/{id}/messages`, `POST /messages/{id}/feedback`, `GET /messages/{id}/export.csv`, `GET /feedback/summary` now exist. `GET /data-sources` and `POST /analytics/preview` now exist.

## 15. Frontend

Present: Home, Data Sources (with datasets, quality panel, joins, schema versions), Business Metrics, Ask Your Data, Reports, Alerts, Activity (with jobs and usage), Settings (with people and two-step sign-in), browser-tested.

Ask Your Data is now a chat (follow-ups, history, feedback, CSV download). Not built: a Dashboards page, invitations by e-mail, Microsoft sign-in button.

## 16. Security and UK compliance

| Control | Status | Detail |
|---|---|---|
| Tenant isolation, roles, lockout, rate limits, headers, production guard | Tested | |
| Two-step sign-in, and organisation policy requiring it | Tested | Browser-tested. Not tested against a real identity provider |
| OIDC / Entra | Partial | Token exchange tested against a **fake** provider; no sign-in button; see `docs/security/oidc-entra.md` |
| Secrets | **Verified** | Key Vault via managed identity in Azure |
| TLS, encryption at rest | Azure defaults | Customer-managed keys not configured |
| Log redaction | Tested | |
| CSV formula injection | Tested | |
| SSRF protection | Not applicable yet | No URL or API connectors exist; must be built with the first one |
| Field-level restrictions and data classification | Not built | Only a refusal rule for salary/payroll questions exists |
| Export, deletion, erasure, retention | Tested | ADLS/Databricks deletion paths not run against real services; backups outside this control |
| DPIA, privacy notice, DPAs, subprocessor and transfer assessments | Not built | Needs the owner and legal/privacy advice. Not something code can supply |

## 17-18. Operations and cost control

| Item | Status | Detail |
|---|---|---|
| Observability (request IDs, JSON logs, spans) | Tested | Not checked against a real Application Insights resource |
| Terraform for the data layer and hosting | **Verified** | Applied by the owner. Missing: private networking, custom domain, WAF |
| CI | Tested | GitHub Actions: api, web, PostgreSQL + pgvector, dbt. Not built: image scanning, staging smoke tests, separate dev/test/staging/prod environments |
| Runbook, rollback, incident response | Partial | `docs/operations/runbook.md`; no incident-response document |
| Tiers, quotas, per-tenant limits | Tested | Plan per organisation (starter / growth / advanced / enterprise) with limits on uploads, rows loaded, questions per day, people and documents; 429 with a clear message at the limit, warning from 80%, shown in Usage and on Home. The numbers are **placeholders, not validated pricing**; the plan is set by the service owner (no customer endpoint). Not built: AI token and embedding budgets, storage-size and compute-cost tracking, billing |
| Lineage source -> model -> metric -> report/alert -> answer | Tested | `GET /lineage` (graph) and `GET /lineage/metrics/{name}` (column-by-column trace to the uploaded file with its checksum, the models built, definition and version, reports/alerts/answers that use it); shown under Business Metrics > Show lineage. Unmapped inputs are reported, never hidden. Not built: lineage for dashboards (there are none yet) and for dbt model dependencies |

## 21. Acceptance scenario

| Step | Status |
|---|---|
| Upload sales, products, customers | Tested + browser locally; **not yet confirmed in the hosted app** (see above) |
| Review schemas, relationships, quality issues; approve mappings; build models | Tested + browser |
| Publish metrics, dashboard | Tested + browser (350 / 140) |
| Revenue or profit answer matches the reference | Tested + browser; **Verified** in Power BI Desktop |
| Trace the answer to source and definition | Tested |
| Unauthorised user cannot reach restricted data | Tested (tenant isolation, roles, two-step policy) |
| Refresh failure recovers without silent record loss | Tested (retries; counts reconcile) |

## 24. Success metrics

Not built: product analytics for activation, time to first dashboard, retention and cost to serve. Per-tenant usage counts exist.

## Priorities for what is left

1. ~~Cost controls: quotas~~ (done, see section 17-18; token budgets and billing still open).
2. ~~Complete the MCP tool set~~ (done).
3. ~~XML upload~~ (done).
4. ~~Lineage view~~ (done).
5. Weekly management summary and a Dashboards page.
6. ~~Conversations (follow-up context), answer feedback and answer export~~ (done; LLM-assisted follow-ups would need a live evaluation run).
7. Things that need the owner: Fabric capacity, a real identity provider, a mail sender, legal review (DPIA), private networking.
