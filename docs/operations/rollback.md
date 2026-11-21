# Rollback

**Status: written, not yet drilled.** Run the drill in `incident-drill.md` once in staging and record the time it took.

## 1. Application (API, worker, web): minutes, no data loss
The previous image tag is still in the registry (tags are never overwritten; use a new tag per release).

Fast path (about a minute, bypasses Terraform; fix Terraform afterwards so the two agree):
```
az containerapp revision list -n datapilot-api -g datapilot-rg -o table
az containerapp revision activate -n datapilot-api -g datapilot-rg --revision <previous revision>
```
Repeat for `datapilot-worker` and `datapilot-web`. In single-revision mode, instead set the image back:
`az containerapp update -n datapilot-api -g datapilot-rg --image <registry>.azurecr.io/datapilot-api:<previous tag>`.

Clean path (what the record of truth should say): re-run Terraform with the previous tags, same secrets as before:
`terraform apply -var deploy_apps=true -var api_image_tag=<previous> -var web_image_tag=<previous>`
(plus the Azure OpenAI variables if the AI planner is on, and the same JWT/MFA values; read them from Key Vault if needed).

Then run the smoke test against the environment.

## 2. Database schema
Migrations run automatically when the API starts and are **forward-only in production**. Rules that make rollback safe:
- Add columns and tables first (expand); remove them only in a later release (contract). The previous image still works
  against the newer schema.
- A release that must change meaning of existing data needs a separate, reviewed plan. Do not ship it as a normal release.
- `alembic downgrade -1` exists for each migration but can drop data. Use it only on a copy.

If data was damaged (not just the code): restore PostgreSQL to a point in time into a **new** server
(`az postgres flexible-server restore --source-server <name> --restore-time <UTC time> --name <new name> -g <rg>`),
check it, then point `database-url` in Key Vault at it and restart the apps. Keep the old server until sure.

## 3. Analytical files and raw uploads
Raw uploads (ADLS) and the analytics files (Azure Files) have soft delete / snapshots only if enabled on the storage account.
Check this in the portal and record the retention you chose here: ______ days.
An organisation's analytical tables can be rebuilt from its raw uploads by re-uploading them.

## 4. After any rollback
Write down what happened and why in the incident record (`incident-response.md`), and add a test for the cause.
