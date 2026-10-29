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
| Source management | Partial | CSV, XLSX, TXT, JSON, JSONL: **Tested** locally. In Azure the web app opens and works (owner), the storage layer was checked directly, but an upload through ClamAV and the worker has **not yet been confirmed** since the Azure Files fix. XML upload: **Tested** (hardened parser, see below). **Web address connector**: a public https file fetched on demand (`POST /data-sources/{id}/sync`, "Fetch now") or every N hours by the worker, through the same validation, malware scan, quotas and ingestion as an upload; an unchanged file (same checksum) is skipped; failures show in Source health: **Tested** with a mocked network only, **not run against a real website**. Not built: connectors that need credentials (OAuth, API keys, bearer tokens: needs a per-organisation secret store), paginated REST APIs and cursors, database connectors. Schema history: **Tested** |
| Automated data engineering | Partial | Schema inference, profiling, mapping suggestions with confidence, quarantine, reconciled counts, retries with backoff, quality checks: **Tested**. Sales files accumulate with upsert by order ID (latest file wins, reported): **Tested**. Not built: lineage view, watermarks / API cursors / CDC, snapshots |
| Business intelligence | Partial | Home dashboard tiles, monthly trend, automatic insights and anomaly flag, "why did it change" driver analysis, saved reports: **Tested**. **Dashboards** page: saved dashboards of up to 12 tiles (approved metrics with trend, saved reports, change explanations), an overview created in one click, every tile recalculated through the same approval gate each time it is opened (a tile whose metric was withdrawn says so instead of showing a stale number), up to 20 per organisation, included in the organisation export: **Tested** (API + browser). Not built: industry templates other than retail, drag-and-drop layout, scheduled report delivery |
| Conversational analytics | Tested | Structured plan, governed metrics, safe SQL, provenance, caveats, refusals: **Tested**; planner **Verified** live 29/29. **Conversations** with follow-ups ("and by region?", "same for gross profit", "what about the last 3 months"): read by rules as a change to the previous plan, shown back as "Understood as: ...", same validation and refusals as any question; private to their owner. Answer **feedback** (helpful / not helpful with a reason; administrators see it on Activity). **CSV export** of an answer (administrators and analysts only, re-runs the plan so approval is re-checked, formula-safe, rate-limited, logged). Chats are in the organisation export and erased with the person, the organisation and by retention. Follow-ups never go to the Azure OpenAI planner (rules only, by design); the 29 live planner cases are single questions and do not exercise them, so follow-up quality is covered by the API and browser tests only |
| Business recommendations | Tested | `GET /recommendations`, shown on Reports: each has evidence, baseline (previous period), confidence with the reasons counted, caveats and what to check. Types: fall, rise, a group with nothing this period, unusual month, data problems (listed first). Fixed wording over governed numbers, so no model writes a figure; it says where numbers moved, not why. Confidence is a simple count of stated reasons, **not a statistical probability**. Not built: forecasts, impact estimates in money, tracking whether a recommendation was acted on |
| RAG knowledge assistant | Tested | Hybrid keyword + vector retrieval, cited answers, tenant isolation: tested on PostgreSQL + pgvector (**Verified** vector queries on Azure PostgreSQL). Azure AI Search backend not built (pgvector is allowed by the proposal) |
| Monitoring and alerts | Partial | Threshold alerts checked by the worker, one log entry per state change, Home banner: **Tested**. **Source health** (failed refresh while it is the latest attempt, changed schema with what changed, file missing when an expected cadence is set) on Data Sources and Home; the worker records each new issue once in the Activity log: **Tested** (API + browser). **Management summary**: created weekly by the worker (last Monday-Sunday) and on request; month-on-month movements from approved metrics, that week's uploads/questions/alerts, what needs attention, caveats; no model writes any number: **Tested**. Not built: e-mail/Teams delivery of alerts and summaries (needs a sender account); the summary is read in the app (Reports) |

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
| Owner, unit, default filters, effective date, example questions, `metric_dimensions` | Partial | Name, description, expression, aggregation, status, version, allowed dimensions exist. **Owner, unit and example questions** are now stored and editable by administrators (Business Metrics > Edit details) without changing the definition, version or approval: **Tested**. Not stored: default filters, effective date, a separate `metric_dimensions` table |

## 9. Power BI

| Item | Status | Detail |
|---|---|---|
| Generated model + DAX | **Verified** | 350 / 3 / 140 in Power BI Desktop. Not built: dimension tables, date table with time intelligence measures (YTD, prior year), RLS/OLS, deployment pipelines |

## 10-13. AI, RAG, MCP, evaluation

| Item | Status | Detail |
|---|---|---|
| Ten-step request lifecycle | Tested | Server-side tenant, hard refusals before the model, plan validated, SQL validated (single SELECT, allowlist, LIMIT), read-only execution, provenance, audit |
| Gold-question set and gates | Partial | 33 planner cases (the first 29 **Verified** live; the four added since have not been run live) plus a security regression suite. `scripts/eval_planner.py --record` now saves a result file per release (commit, mode, per-case result, planner used, median/slowest latency, repeated runs) and flags regressions against the previous file; stored as files in `docs/evaluations/`, not a database table. A rules-only baseline is recorded (31/33). **No live record exists yet**: the owner must run it with the Azure key. Not measured: token counts and cost |
| MCP tools | Tested | 12 read-only tools cover all 8 the proposal names (`ask_data` is its `run_approved_query`). Not tested with a specific MCP client app |
| Copilot readiness | Not built | Needs Fabric/Power BI licensing |

## 14. Metadata database and API

Present: tenants, users, memberships, identities, data_sources, source_files, ingestion_jobs, schema_versions, datasets, quality results, metrics, reports, alerts, analytical_queries, audit_events, documents, chunks, deletion log.

Not built: `workspaces`, `data_models`, `model_dependencies`, `data_quality_rules` (rules are fixed in code), `metric_dimensions`, `ai_evaluations`. Added: `conversations`, `conversation_messages` (with feedback), `summaries`, `dashboards`.

API gaps against the illustrative list: `POST /data-sources/{id}/sync`. `POST /conversations/{id}/messages`, `POST /messages/{id}/feedback`, `GET /messages/{id}/export.csv`, `GET /feedback/summary` now exist. `GET /data-sources` and `POST /analytics/preview` now exist.

## 15. Frontend

Present: Home, Data Sources (with datasets, quality panel, joins, schema versions), Business Metrics, Ask Your Data, Reports, Alerts, Activity (with jobs and usage), Settings (with people and two-step sign-in), browser-tested.

Ask Your Data is now a chat (follow-ups, history, feedback, CSV download). Dashboards page: **Tested**. Not built: invitations by e-mail, Microsoft sign-in button.

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
| SSRF protection | Tested | `services/netguard.py`: https on 443 only, no credentials in the address, local-style names refused, every resolved address must be public (private, loopback, link-local/metadata, CGNAT, multicast, IPv4-in-IPv6 all refused), connection pinned to the checked address (DNS rebinding), redirects followed by hand (max 3) and re-checked each hop, proxy settings ignored, size cap on the decoded body, time limit; query strings (pre-signed tokens) kept out of audit and HTTP logs. Mocked DNS and HTTP in tests; not penetration-tested. Egress filtering at the network level (Terraform/NSG) is still advisable and not built |
| Field-level restrictions and data classification | Not built | Only a refusal rule for salary/payroll questions exists |
| Export, deletion, erasure, retention | Tested | ADLS/Databricks deletion paths not run against real services; backups outside this control |
| DPIA, privacy notice, DPAs, subprocessor and transfer assessments | Not built | Needs the owner and legal/privacy advice. Not something code can supply |

## 17-18. Operations and cost control

| Item | Status | Detail |
|---|---|---|
| Observability (request IDs, JSON logs, spans) | Tested | Not checked against a real Application Insights resource |
| Terraform for the data layer and hosting | **Verified** | Applied by the owner. Missing: private networking, custom domain, WAF |
| CI | Tested | GitHub Actions: api, web, PostgreSQL + pgvector, dbt. Not built: image scanning, staging smoke tests, separate dev/test/staging/prod environments |
| Runbook, rollback, incident response | Partial | `docs/operations/runbook.md`; `docs/operations/incident-response.md` is a **starter outline** (not legal advice; contacts table is blank for the owner to fill). No tested image rollback procedure or incident drill |
| Tiers, quotas, per-tenant limits | Tested | Plan per organisation (starter / growth / advanced / enterprise) with limits on uploads, rows loaded, questions per day, people and documents; 429 with a clear message at the limit, warning from 80%, shown in Usage and on Home. The numbers are **placeholders, not validated pricing**; the plan is set by the service owner (no customer endpoint). Not built: AI token and embedding budgets, storage-size and compute-cost tracking, billing |
| Lineage source -> model -> metric -> report/alert -> answer | Tested | `GET /lineage` (graph) and `GET /lineage/metrics/{name}` (column-by-column trace to the uploaded file with its checksum, the models built, definition and version, reports/alerts/answers that use it); shown under Business Metrics > Show lineage. Unmapped inputs are reported, never hidden. Not built: lineage for dashboards and for dbt model dependencies |

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
5. ~~Weekly management summary~~ (done, in-app) and ~~a Dashboards page~~ (done).
6. ~~Conversations (follow-up context), answer feedback and answer export~~ (done; LLM-assisted follow-ups would need a live evaluation run).
7. ~~Written recommendations~~ (done) and ~~web address connector with SSRF protection~~ (done, no credentials).
8. Things that need the owner: Fabric capacity, a real identity provider, a mail sender, legal review (DPIA), private networking.
