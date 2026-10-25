# Hosting for the API, worker, web app and ClamAV on Azure Container Apps.
#
# OFF by default (deploy_apps = false), so applying main.tf alone behaves exactly as before.
# What this adds when on: container registry, Key Vault (secrets read through a managed identity),
# one Container Apps environment, and four apps. Only the web app is public; the API, worker and
# ClamAV are reachable only inside the environment.
#
# Honest limits of this starter (see docs/operations/azure-starter-deploy.md):
#  - PostgreSQL is reached over the public endpoint with TLS, opened to Azure services (0.0.0.0 rule).
#    Private networking (VNet + private endpoints) is the next hardening step.
#  - The web app is served on the default *.azurecontainerapps.io address with Azure-managed TLS;
#    no custom domain, WAF or Front Door yet.
#  - Not yet applied by anyone: run `terraform validate` and `terraform plan` first and read the plan.

variable "deploy_apps" {
  type        = bool
  default     = false
  description = "Create the registry, Key Vault and Container Apps. Needs the images to exist (see the deploy doc)."
}

variable "api_image_tag" {
  type        = string
  default     = "latest"
  description = "Tag of the datapilot-api image in the registry."
}

variable "web_image_tag" {
  type        = string
  default     = "latest"
  description = "Tag of the datapilot-web image in the registry."
}

variable "jwt_secret" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Random string of at least 32 characters. Stored in Key Vault. Set with TF_VAR_jwt_secret."
  validation {
    condition     = var.jwt_secret == "" || length(var.jwt_secret) >= 32
    error_message = "jwt_secret must be at least 32 characters (or empty while deploy_apps is false)."
  }
}

variable "mfa_encryption_key" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Separate random string of at least 32 characters for MFA secrets. Set with TF_VAR_mfa_encryption_key."
  validation {
    condition     = var.mfa_encryption_key == "" || length(var.mfa_encryption_key) >= 32
    error_message = "mfa_encryption_key must be at least 32 characters (or empty while deploy_apps is false)."
  }
}

locals {
  apps_enabled = var.deploy_apps ? 1 : 0
  suffix       = substr(md5(azurerm_resource_group.rg.id), 0, 6)
  api_name     = "${var.prefix}-api"
  clam_name    = "${var.prefix}-clamav"
  web_name     = "${var.prefix}-web"
  worker_name  = "${var.prefix}-worker"
  # The web app proxies /api/* to the API by its in-environment name, so the web image can be built before anything exists.
  api_internal_url = "http://${local.api_name}"
  database_url     = "postgresql+psycopg2://${azurerm_postgresql_flexible_server.meta.administrator_login}:${urlencode(var.pg_admin_password)}@${azurerm_postgresql_flexible_server.meta.fqdn}:5432/${azurerm_postgresql_flexible_server_database.app.name}?sslmode=require"
}

resource "terraform_data" "apps_guard" {
  count = local.apps_enabled
  lifecycle {
    precondition {
      condition     = var.jwt_secret != "" && var.mfa_encryption_key != "" && var.jwt_secret != var.mfa_encryption_key
      error_message = "With deploy_apps = true, set TF_VAR_jwt_secret and TF_VAR_mfa_encryption_key to two different random strings of 32+ characters."
    }
  }
}

resource "azurerm_container_registry" "acr" {
  count               = local.apps_enabled
  name                = "${var.prefix}acr${local.suffix}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_user_assigned_identity" "apps" {
  count               = local.apps_enabled
  name                = "${var.prefix}-apps-id"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
}

resource "azurerm_role_assignment" "apps_acr_pull" {
  count                = local.apps_enabled
  scope                = azurerm_container_registry.acr[0].id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.apps[0].principal_id
}

resource "azurerm_role_assignment" "apps_blob" {
  count                = local.apps_enabled
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.apps[0].principal_id
}

resource "azurerm_key_vault" "kv" {
  count                      = local.apps_enabled
  name                       = "${var.prefix}-kv-${local.suffix}"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false # starter: lets `terraform destroy` + re-apply work; turn on for production
  soft_delete_retention_days = 7
}

resource "azurerm_role_assignment" "me_kv" {
  count                = local.apps_enabled
  scope                = azurerm_key_vault.kv[0].id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "apps_kv" {
  count                = local.apps_enabled
  scope                = azurerm_key_vault.kv[0].id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.apps[0].principal_id
}

resource "azurerm_key_vault_secret" "jwt" {
  count        = local.apps_enabled
  name         = "jwt-secret"
  value        = var.jwt_secret
  key_vault_id = azurerm_key_vault.kv[0].id
  depends_on   = [azurerm_role_assignment.me_kv, terraform_data.apps_guard]
}

resource "azurerm_key_vault_secret" "mfa" {
  count        = local.apps_enabled
  name         = "mfa-encryption-key"
  value        = var.mfa_encryption_key
  key_vault_id = azurerm_key_vault.kv[0].id
  depends_on   = [azurerm_role_assignment.me_kv, terraform_data.apps_guard]
}

resource "azurerm_key_vault_secret" "database_url" {
  count        = local.apps_enabled
  name         = "database-url"
  value        = local.database_url
  key_vault_id = azurerm_key_vault.kv[0].id
  depends_on   = [azurerm_role_assignment.me_kv]
}

# Azure services (the container apps) must be able to reach PostgreSQL. 0.0.0.0-0.0.0.0 is Azure's "allow Azure services" rule.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  count            = local.apps_enabled
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.meta.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_container_app_environment" "env" {
  count                      = local.apps_enabled
  name                       = "${var.prefix}-env"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id
}

# ---- ClamAV: internal TCP only; the API refuses uploads if it cannot reach it (fail-closed) ----
resource "azurerm_container_app" "clamav" {
  count                        = local.apps_enabled
  name                         = local.clam_name
  container_app_environment_id = azurerm_container_app_environment.env[0].id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  template {
    min_replicas = 1
    max_replicas = 1
    container {
      name   = "clamav"
      image  = "docker.io/clamav/clamav:stable"
      cpu    = 1.5 # Container Apps only accepts fixed pairs: 1.5 CPU goes with 3 GiB
      memory = "3Gi" # signatures are held in memory
    }
  }

  ingress {
    external_enabled = false
    target_port      = 3310
    exposed_port     = 3310
    transport        = "tcp"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

# ---- API: internal only; applies migrations on start ----
resource "azurerm_container_app" "api" {
  count                        = local.apps_enabled
  name                         = local.api_name
  container_app_environment_id = azurerm_container_app_environment.env[0].id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps[0].id]
  }
  registry {
    server   = azurerm_container_registry.acr[0].login_server
    identity = azurerm_user_assigned_identity.apps[0].id
  }
  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.database_url[0].versionless_id
    identity            = azurerm_user_assigned_identity.apps[0].id
  }
  secret {
    name                = "jwt-secret"
    key_vault_secret_id = azurerm_key_vault_secret.jwt[0].versionless_id
    identity            = azurerm_user_assigned_identity.apps[0].id
  }
  secret {
    name                = "mfa-key"
    key_vault_secret_id = azurerm_key_vault_secret.mfa[0].versionless_id
    identity            = azurerm_user_assigned_identity.apps[0].id
  }

  template {
    min_replicas = 1
    max_replicas = 2
    container {
      name    = "api"
      image   = "${azurerm_container_registry.acr[0].login_server}/datapilot-api:${var.api_image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "JWT_SECRET"
        secret_name = "jwt-secret"
      }
      env {
        name        = "MFA_ENCRYPTION_KEY"
        secret_name = "mfa-key"
      }
      env {
        name  = "STORAGE_BACKEND"
        value = "adls"
      }
      env {
        name  = "ADLS_ACCOUNT_URL"
        value = "https://${azurerm_storage_account.lake.name}.dfs.core.windows.net"
      }
      env {
        name  = "ADLS_FILESYSTEM"
        value = azurerm_storage_data_lake_gen2_filesystem.raw.name
      }
      env {
        name  = "AZURE_CLIENT_ID" # tells DefaultAzureCredential which managed identity to use
        value = azurerm_user_assigned_identity.apps[0].client_id
      }
      env {
        name  = "MALWARE_SCAN"
        value = "clamav"
      }
      env {
        name  = "CLAMAV_HOST"
        value = local.clam_name
      }
      env {
        name  = "TRUST_PROXY_HEADERS" # the API is reachable only through the web app's proxy
        value = "true"
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.ai.connection_string
      }
    }
  }

  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "http"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  depends_on = [
    azurerm_role_assignment.apps_acr_pull,
    azurerm_role_assignment.apps_kv,
    azurerm_role_assignment.apps_blob,
    azurerm_postgresql_flexible_server_firewall_rule.azure_services,
  ]
}

# ---- Worker: same image, no ingress ----
resource "azurerm_container_app" "worker" {
  count                        = local.apps_enabled
  name                         = local.worker_name
  container_app_environment_id = azurerm_container_app_environment.env[0].id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps[0].id]
  }
  registry {
    server   = azurerm_container_registry.acr[0].login_server
    identity = azurerm_user_assigned_identity.apps[0].id
  }
  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.database_url[0].versionless_id
    identity            = azurerm_user_assigned_identity.apps[0].id
  }
  secret {
    name                = "jwt-secret"
    key_vault_secret_id = azurerm_key_vault_secret.jwt[0].versionless_id
    identity            = azurerm_user_assigned_identity.apps[0].id
  }
  secret {
    name                = "mfa-key"
    key_vault_secret_id = azurerm_key_vault_secret.mfa[0].versionless_id
    identity            = azurerm_user_assigned_identity.apps[0].id
  }

  template {
    min_replicas = 1
    max_replicas = 1
    container {
      name    = "worker"
      image   = "${azurerm_container_registry.acr[0].login_server}/datapilot-api:${var.api_image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "app.worker"]

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "JWT_SECRET"
        secret_name = "jwt-secret"
      }
      env {
        name        = "MFA_ENCRYPTION_KEY"
        secret_name = "mfa-key"
      }
      env {
        name  = "STORAGE_BACKEND"
        value = "adls"
      }
      env {
        name  = "ADLS_ACCOUNT_URL"
        value = "https://${azurerm_storage_account.lake.name}.dfs.core.windows.net"
      }
      env {
        name  = "ADLS_FILESYSTEM"
        value = azurerm_storage_data_lake_gen2_filesystem.raw.name
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.apps[0].client_id
      }
      env {
        name  = "MALWARE_SCAN"
        value = "clamav"
      }
      env {
        name  = "CLAMAV_HOST"
        value = local.clam_name
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.ai.connection_string
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.apps_acr_pull,
    azurerm_role_assignment.apps_kv,
    azurerm_role_assignment.apps_blob,
    azurerm_container_app.api,
  ]
}

# ---- Web: the only public endpoint ----
resource "azurerm_container_app" "web" {
  count                        = local.apps_enabled
  name                         = local.web_name
  container_app_environment_id = azurerm_container_app_environment.env[0].id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps[0].id]
  }
  registry {
    server   = azurerm_container_registry.acr[0].login_server
    identity = azurerm_user_assigned_identity.apps[0].id
  }

  template {
    min_replicas = 1
    max_replicas = 2
    container {
      name   = "web"
      image  = "${azurerm_container_registry.acr[0].login_server}/datapilot-web:${var.web_image_tag}"
      cpu    = 0.5
      memory = "1Gi"
      env {
        name  = "API_URL"
        value = local.api_internal_url
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 3000
    transport        = "http"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  depends_on = [azurerm_role_assignment.apps_acr_pull, azurerm_container_app.api]
}

output "acr_name" {
  value = try(azurerm_container_registry.acr[0].name, "")
}

output "web_url" {
  value = try("https://${azurerm_container_app.web[0].ingress[0].fqdn}", "")
}

output "web_image_build_arg" {
  description = "Pass as --build-arg API_URL=... when building the web image."
  value       = local.api_internal_url
}
