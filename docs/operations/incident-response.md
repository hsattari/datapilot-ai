# Incident response (starter)

A working outline for a small team, written against what this system can actually do. It is **not** a legal
document: who must be told, and by when, depends on your contracts and on advice from a privacy or legal adviser
(for personal data under UK GDPR, the Information Commissioner's Office expects notification of a reportable breach
within 72 hours of becoming aware of it). Fill in the names and numbers in the last section before you need them.

## 1. Decide what kind of incident it is

| Signal | Likely kind | First look |
|---|---|---|
| A customer sees another organisation's data | **Confidentiality breach** (treat as the most serious) | Section 3 |
| Unknown sign-ins, repeated lockouts, strange `auth.*` events | Account compromise | Section 4 |
| Malware verdict on an upload, or scanner unavailable | Malicious file | Section 5 |
| Wrong numbers, a failed refresh, schema changes | Data quality (not a security incident unless data was exposed) | Source health, Activity |
| Service down or very slow | Availability | `docs/operations/runbook.md` |
| A leaked secret (key, password, token) | Credential exposure | Section 6 |

## 2. First hour, for any security incident

1. **Write down the time you learned of it** and who found it. The clock for any notification duty starts then.
2. **Keep evidence.** Do not delete anything. Take a copy of the relevant Activity events (Settings > Your data, export, or the audit table) and Container Apps logs for the period.
3. **Contain** with the narrowest step that works (sections 3 to 6). Prefer disabling an account or rotating a secret over shutting everything down.
4. **Tell one person who decides** (the incident lead) and keep a running log: what was seen, what was done, by whom, when.
5. Do not discuss details in public channels or with customers until the lead has agreed the wording.

## 3. A tenant boundary may have failed

- Reproduce with two test organisations; note the request path and the IDs involved. Cross-organisation IDs should return **404**.
- Contain: take the affected release out (redeploy the previous image tag by setting the app's image to the earlier tag in Azure or Terraform (the runbook only covers database migration rollback, `alembic downgrade -1`)). If it cannot be isolated, scale the API app to zero while you fix it.
- Scope: the audit log records tenant, user and action for every request that changed state, and each answer records its query. Use them to list which organisations' data could have been read.
- Add a regression test that fails before the fix, then ship the fix.

## 4. Account compromise

- Administrators can deactivate a user and reset their two-step sign-in in Settings > Users. Passwords are changed by the person; sessions end when the token expires, so also rotate `JWT_SECRET` if tokens may be stolen (this signs everybody out).
- Turn on the organisation's "Require two-step sign-in" policy if it is off.
- Check `auth.failed`, `auth.locked`, `auth.login`, `auth.mfa_enabled`/`auth.mfa_disabled` and `auth.recovery_code_used` events for the account and its IP pattern.

## 5. Malicious file

- Uploads with a positive malware verdict are refused and never stored; the event is `upload.malware_detected` (and `source.sync_failed` for web connectors). Review who uploaded or which address was connected.
- If the scanner is unavailable, uploads are refused (unless `MALWARE_SCAN_FAIL_OPEN` was deliberately set, which production does not allow). Restore the ClamAV app, then ask people to retry.

## 6. Leaked secret

1. Rotate it at the source (Azure OpenAI key, storage key, database password, JWT secret, registry credentials).
2. Update the value in Key Vault (and in GitHub secrets if it is used by the workflow) and restart the apps so they pick it up.
3. Check provider logs for use of the old value during the exposure window.
4. If it was committed to git, rotating is what protects you; removing it from history is secondary.

## 7. After the incident

- Within a week: a short written review (what happened, impact, how it was found, what stopped it, what changes). Blame-free.
- Record the follow-up actions as tracked work, with owners and dates.
- Update this document and `docs/security/controls.md` if a control was missing or wrong.
- If personal data was involved, keep the notification decision and its reasoning (even if you decided not to notify).

## 8. Fill in before you need it

| Role | Name | Phone / contact |
|---|---|---|
| Incident lead | | |
| Deputy | | |
| Azure subscription owner | | |
| Privacy / legal adviser | | |
| Customer communications | | |
