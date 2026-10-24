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
