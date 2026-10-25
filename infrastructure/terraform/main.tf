# Starter-profile Azure DATA infrastructure: storage (ADLS Gen2), PostgreSQL, logging, Application Insights.
# Verified by the project owner: `terraform init` and `terraform validate` pass (Terraform 1.16.6, azurerm 4.81.0).
# NOT yet verified: `plan` / `apply`. NOT included yet: hosting for the API, worker and web app (Container Apps),
# Key Vault, private networking and the ClamAV scanner - so applying this does not by itself give you a running app.
# Review SKUs, networking and policies before use.
terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  # Set the subscription with ARM_SUBSCRIPTION_ID (required by provider 4.x) and sign in with `az login`.
}

variable "location" {
  type    = string
  default = "uksouth"
}

variable "prefix" {
  type        = string
  default     = "datapilot"
  description = "Lower-case letters and digits only: it becomes part of the storage account name."
  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.prefix))
    error_message = "prefix must be 3-12 lower-case letters or digits."
  }
}

variable "create_openai" {
  type        = bool
  default     = false
  description = "Create a new Azure OpenAI resource. Leave false if you already have one (the app only needs its endpoint, key and deployment names)."
}

variable "allowed_ip" {
  type        = string
  default     = ""
  description = "Your public IPv4 address. Opens the PostgreSQL firewall to that one address (for testing from your laptop). Empty = nobody can connect."
  validation {
    condition     = var.allowed_ip == "" || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.allowed_ip))
    error_message = "allowed_ip must be empty or an IPv4 address such as 203.0.113.7."
  }
}

variable "log_daily_cap_gb" {
  type        = number
  default     = 1
  description = "Daily ingestion cap (GB) for Log Analytics and Application Insights: a cost guard rail."
}

variable "pg_admin_password" {
  type      = string
  sensitive = true
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "rg" {
  name     = "${var.prefix}-rg"
  location = var.location
}

resource "azurerm_storage_account" "lake" {
  name                            = "${var.prefix}lake${substr(md5(azurerm_resource_group.rg.id), 0, 6)}"
  resource_group_name             = azurerm_resource_group.rg.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "GRS"
  is_hns_enabled                  = true # ADLS Gen2
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_data_lake_gen2_filesystem" "raw" {
  name               = "raw"
  storage_account_id = azurerm_storage_account.lake.id
}

resource "azurerm_postgresql_flexible_server" "meta" {
  # Server names are global (they become <name>.postgres.database.azure.com), so add a stable suffix like the storage account.
  name                   = "${var.prefix}-pg-${substr(md5(azurerm_resource_group.rg.id), 0, 6)}"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = var.location
  version                = "16"
  administrator_login    = "datapilot"
  administrator_password = var.pg_admin_password
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768

  lifecycle {
    # Azure picks an availability zone when none is set; the provider then wants to "change" it, which Azure refuses.
    ignore_changes = [zone]
  }
}

# Azure only allows CREATE EXTENSION for allow-listed extensions. Migration 0002 needs pgvector ("vector").
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.meta.id
  value     = "vector"
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "me" {
  count            = var.allowed_ip == "" ? 0 : 1
  name             = "allowed-ip"
  server_id        = azurerm_postgresql_flexible_server.meta.id
  start_ip_address = var.allowed_ip
  end_ip_address   = var.allowed_ip
}

# The API's ADLS backend signs in with Microsoft Entra (DefaultAzureCredential): the deploying user needs data access.
resource "azurerm_role_assignment" "me_blob" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = "datapilot"
  server_id = azurerm_postgresql_flexible_server.meta.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "${var.prefix}-logs"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  retention_in_days   = 30
  daily_quota_gb      = var.log_daily_cap_gb
}

resource "azurerm_application_insights" "ai" {
  name                = "${var.prefix}-appi"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  workspace_id        = azurerm_log_analytics_workspace.logs.id
  application_type    = "web"

  daily_data_cap_in_gb = var.log_daily_cap_gb
}

resource "azurerm_cognitive_account" "openai" {
  count                 = var.create_openai ? 1 : 0
  name                  = "${var.prefix}-openai"
  custom_subdomain_name = "${var.prefix}-openai-${substr(md5(azurerm_resource_group.rg.id), 0, 6)}"
  resource_group_name   = azurerm_resource_group.rg.name
  location              = var.location
  kind                  = "OpenAI"
  sku_name              = "S0"
  # Model deployments are created separately; verify regional model availability.
}

output "storage_account_name" {
  value = azurerm_storage_account.lake.name
}

output "adls_account_url" {
  value = "https://${azurerm_storage_account.lake.name}.dfs.core.windows.net" # ADLS_ACCOUNT_URL in .env
}

output "database_url_template" {
  value = "postgresql+psycopg2://datapilot:<password>@${azurerm_postgresql_flexible_server.meta.fqdn}:5432/datapilot?sslmode=require"
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.meta.fqdn
}

output "applicationinsights_connection_string" {
  value     = azurerm_application_insights.ai.connection_string
  sensitive = true # put into APPLICATIONINSIGHTS_CONNECTION_STRING of the API (ideally via Key Vault)
}

output "openai_endpoint" {
  value = try(azurerm_cognitive_account.openai[0].endpoint, null)
}
