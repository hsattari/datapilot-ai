# Azure Starter infrastructure: create, check, remove

What `infrastructure/terraform` creates (all in one resource group `datapilot-rg`, region `uksouth`):
ADLS Gen2 storage account + `raw` file system, PostgreSQL flexible server (Burstable B1ms) with a `datapilot` database,
pgvector allow-listed, Log Analytics + Application Insights (1 GB/day cap), an optional firewall rule for your IP, and
"Storage Blob Data Contributor" for the signed-in user. **Not included yet:** hosting for the API/worker/web app,
Key Vault, private networking, ClamAV. Applying this does not give you a running application.

Rough running cost while idle: the PostgreSQL server dominates (about USD 14 a month compute in UK South per a third-party
price list, plus storage); storage and logging are pennies at test volumes. Confirm in the Azure pricing calculator.

## Create
```
az login --tenant <tenant-id>
cd infrastructure/terraform
terraform init
$env:ARM_SUBSCRIPTION_ID = "<subscription id>"
$env:TF_VAR_pg_admin_password = "<a strong password; keep it in your password manager>"
$env:TF_VAR_allowed_ip = "<your public IPv4, e.g. from https://api.ipify.org>"
terraform validate
terraform plan -out tfplan
terraform apply tfplan
```

## Check (from the repo root)
Put the outputs into `.env` (`terraform output adls_account_url`, `terraform output database_url_template`), then:
```
python scripts/test_azure_resources.py
cd apps/api; alembic upgrade head         # runs migrations 0001-... including CREATE EXTENSION vector
```

## Remove everything (stops all charges)
```
terraform destroy
```

## Part 2: run the app in Azure (optional, `deploy_apps = true`)

Status: **written, not yet validated or applied by anyone.** `infrastructure/terraform/hosting.tf` is off by default, so Part 1 behaves as before. Read the plan before applying.

What it adds: container registry (Basic), Key Vault (JWT secret, MFA key, database URL; read by a managed identity), a Container Apps environment, and four apps. Only the **web** app is public. The **API**, **worker** and **ClamAV** are reachable only inside the environment. The API applies migrations on start. Uploads go to ADLS through the managed identity; there are no storage keys.

Extra cost, roughly: Container Apps (4 always-on replicas, ClamAV needs 3 GiB), registry (Basic is a few USD a month), Key Vault (cents). Expect tens of USD a month on top of Part 1 - check the Azure pricing calculator before applying, and destroy when finished.

Known limits: PostgreSQL is public with TLS and an "allow Azure services" rule (private networking is the next step); default `*.azurecontainerapps.io` address, no custom domain or WAF; no Entra sign-in button in the web UI yet; Azure OpenAI and Databricks settings are not wired in (the planner uses its rule-based fallback).

### Steps (PowerShell, in `infrastructure\terraform`)

1. Create the registry first, so the images have somewhere to go. Use two different random strings of 32+ characters (generate them, never type them into chat):

```powershell
$env:TF_VAR_jwt_secret = [Convert]::ToBase64String((1..48 | % { Get-Random -Maximum 256 }) -as [byte[]])
$env:TF_VAR_mfa_encryption_key = [Convert]::ToBase64String((1..48 | % { Get-Random -Maximum 256 }) -as [byte[]])
terraform validate
terraform plan -var deploy_apps=true '-target=azurerm_container_registry.acr[0]'
terraform apply -var deploy_apps=true '-target=azurerm_container_registry.acr[0]'
```

2. Build and push the images in Azure (no local Docker needed). Run from the repo root:

```powershell
$acr = terraform -chdir=infrastructure/terraform output -raw acr_name
az acr build -r $acr -t datapilot-api:latest -f infrastructure/docker/api.Dockerfile .
az acr build -r $acr -t datapilot-web:latest -f infrastructure/docker/web.Dockerfile --build-arg API_URL=http://datapilot-api .
```

(`http://datapilot-api` is `<prefix>-api`; change it if you changed `prefix`.)

3. Apply everything and open the web address:

```powershell
cd infrastructure\terraform
terraform plan -var deploy_apps=true
terraform apply -var deploy_apps=true
terraform output web_url
```

4. Check: open `web_url`, register, upload a CSV, ask a question. The first start is slow while ClamAV downloads signatures; uploads are refused (503) until it answers. Logs are in the Log Analytics workspace (`ContainerAppConsoleLogs_CL`).

5. Stop the charges: `terraform destroy -var deploy_apps=true`.
