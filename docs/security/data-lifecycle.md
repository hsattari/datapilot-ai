# Data lifecycle: export, erasure, retention

Purpose: let a customer (the controller) exercise UK GDPR rights over the data DataPilot AI processes for them (access and
portability, erasure), and leave the platform cleanly. This is engineering documentation, not legal advice; the DPA,
retention schedule and DPIA still need legal review.

## What exists (tested)
| Capability | How | Notes |
|---|---|---|
| Export everything for an organisation | `GET /api/v1/export/tenant[?include_raw=true]` (admin) or Settings > Export your data | ZIP: `metadata/*.json` (records, questions, audit events, documents' text), `data/*.csv` (modelled tables: fact, dimensions, quarantine), optional `raw/` originals, `manifest.json` with SHA-256 per file. Never contains password hashes, MFA secrets or recovery codes. Text cells starting `= + - @` are prefixed with `'` so spreadsheets do not execute them. Rate limited (3/min/user). Audited. |
| Delete an organisation | `POST /api/v1/tenant/delete` (admin) or Settings > Delete this organisation | Needs the exact organisation name, the user's password and, if two-step sign-in is on, an authenticator or recovery code. Removes raw files, the analytical store (DuckDB file / Databricks schema), and every tenant-scoped row (children before parents, derived from the schema so new tables are included). People who belong to no other organisation are erased. Immediate, no grace period. |
| Erase one person | `DELETE /api/v1/users/{id}` (admin) or Settings > People > Erase | E-mail, password hash, MFA data and sign-in links are removed or replaced; the e-mail is scrubbed from audit-event details; an anonymous placeholder row (opaque id, unusable password) remains so audit events keep a valid reference. The last active administrator cannot be erased. |
| Proof of deletion | `deletion_log` table | Tenant id, opaque requester id, per-table counts, time. No name, e-mail or business data. |
| Retention | `RETENTION_QUERIES_DAYS` (default 365), `RETENTION_AUDIT_DAYS` (default 730); `0` keeps forever | The worker purges expired question history and audit events at most hourly. Raw files, datasets and models are NOT purged automatically: the customer controls those through deletion. |

Failure behaviour: external stores (files, analytical store) are deleted first and database rows last, inside one transaction.
If an external delete fails, nothing in the database has changed and the request can simply be repeated (tested).

## Limits you must know about (not hidden)
1. **Backups.** Deleting here does not reach Azure-side copies: PostgreSQL point-in-time/geo backups, storage soft-delete
   or snapshots, Databricks time travel/Delta history (`VACUUM`), and any replicas. Those expire on the retention you configure
   in Azure; the customer-facing DPA must state that period. Configure short soft-delete windows for tenant data.
2. **Logs and traces** hold opaque ids only (tested), but Application Insights/Log Analytics retention is set in Azure.
3. **Free-text questions** typed by users stay in the organisation's records (the customer is the controller) until the
   organisation is deleted or the retention period passes; erasing a person does not rewrite them.
4. **Export is synchronous and built in memory.** Fine for Starter-size organisations (uploads are capped at 25 MB each);
   large tenants need an asynchronous job that writes to blob storage and returns an expiring link.
5. **Not verified against real services:** `AdlsStorage.delete_prefix` (ADLS Gen2) has never run against a real storage
   account, and `DatabricksBackend.drop_tenant` / `export_table` are mock-tested only. Run both once in a test subscription/workspace before relying on them.
6. No grace period or soft-delete for organisations; consider one (e.g. 14 days) before launch.
7. The anonymous placeholder row left by an erased person has no personal data but is not removed.
