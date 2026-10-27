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
| Source management | Partial | CSV, XLSX, TXT, JSON, JSONL: **Tested** locally. In Azure the web app opens and works (owner), the storage layer was checked directly, but an upload through ClamAV and the worker has **not yet been confirmed** since the Azure Files fix. Not built: XML, API connectors, database connectors, `POST /data-sources/{id}/sync`. Schema history: **Tested** |
| Automated data engineering | Partial | Schema inference, profiling, mapping suggestions with confidence, quarantine, reconciled counts, retries with backoff, quality checks: **Tested**. Sales files accumulate with upsert by order ID (latest file wins, reported): **Tested**. Not built: lineage view, watermarks / API cursors / CDC, snapshots |
| Business intelligence | Partial | Home dashboard tiles, monthly trend, automatic insights and anomaly flag, "why did it change" driver analysis, saved reports: **Tested**. Not built: industry templates other than retail, a separate Dashboards page with saved dashboards, scheduled report delivery, weekly management summary |
| Conversational analytics | Partial | Structured plan, governed metrics, safe SQL, provenance, caveats, refusals: **Tested**; planner **Verified** live 29/29. Not built: follow-up context (no conversation history), answer feedback, permission-controlled export of an answer |
| Business recommendations | Partial | Evidence-based driver analysis and anomaly flags with caveats. Not built: written recommendations with baselines, confidence and next steps |
| RAG knowledge assistant | Tested | Hybrid keyword + vector retrieval, cited answers, tenant isolation: tested on PostgreSQL + pgvector (**Verified** vector queries on Azure PostgreSQL). Azure AI Search backend not built (pgvector is allowed by the proposal) |
| Monitoring and alerts | Partial | Threshold alerts checked by the worker, one log entry per state change, Home banner: **Tested**. Not built: e-mail/Teams delivery (needs a sender account), alerts for missing files, refresh failures and schema changes as their own rules, weekly summary |

## 4-7. Architecture, ingestion, medallion, dbt

| Item | Status | Detail |
|---|---|---|
| Next.js + FastAPI + PostgreSQL + ADLS Gen2 | **Verified** | Running in Azure Container Apps; ADLS and PostgreSQL checked by `scripts/test_azure_resources.py` |
| Starter profile (DuckDB) | Tested | One file per tenant on an Azure Files share in Azure. SMB has no byte-range locks; worker and API open the file one after the other. Not load-tested |
| dbt Core retail template | **Verified** | `dbt build` 8/8 pass (dbt 1.12 + DuckDB), also in CI. Other warehouses not tested. Not built: snapshots, exposures |
| File validation, size/parser limits, checksum | Tested | Malware scanning: tested against a **fake** ClamAV only |
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
| MCP tools | Partial | 6 read-only tools exist. The proposal names 8; missing: `list_data_sources`, `get_dataset_profile`, `get_metric_definition`, `preview_analytical_query`, `get_pipeline_status`, `get_data_quality_summary` |
| Copilot readiness | Not built | Needs Fabric/Power BI licensing |

## 14. Metadata database and API

Present: tenants, users, memberships, identities, data_sources, source_files, ingestion_jobs, schema_versions, datasets, quality results, metrics, reports, alerts, analytical_queries, audit_events, documents, chunks, deletion log.

Not built: `workspaces`, `data_models`, `model_dependencies`, `data_quality_rules` (rules are fixed in code), `metric_dimensions`, `dashboards`, `conversations`, `conversation_messages`, `ai_evaluations`.

API gaps against the illustrative list: `POST /data-sources/{id}/sync`, `GET /data-sources`, `/conversations/{id}/messages`.

## 15. Frontend

Present: Home, Data Sources (with datasets, quality panel, joins, schema versions), Business Metrics, Ask Your Data, Reports, Alerts, Activity (with jobs and usage), Settings (with people and two-step sign-in), browser-tested.

Not built: a Dashboards page, follow-up chat, feedback buttons, exports of answers, invitations by e-mail, Microsoft sign-in button.

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
| Lineage source -> model -> metric -> dashboard -> answer | Partial | Each answer carries its metric definition, source and freshness; there is no lineage view |

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
2. Complete the MCP tool set (read-only wrappers over existing endpoints).
3. XML upload.
4. Lineage view and a Data Sources list endpoint.
5. Weekly management summary and a Dashboards page.
6. Conversations (follow-up context), answer feedback and answer export.
7. Things that need the owner: Fabric capacity, a real identity provider, a mail sender, legal review (DPIA), private networking.
