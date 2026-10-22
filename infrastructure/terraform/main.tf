# Starter-profile Azure infrastructure.
# Verified: the syntax parses and is formatted (`tofu fmt`, OpenTofu 1.8). NOT verified: provider schema (`validate`),
# `plan` or `apply` - the build sandbox cannot download the azurerm provider. Run `terraform init && terraform
# validate && terraform plan` yourself and review SKUs, networking (private endpoints) and policies before use.
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

variable "pg_admin_password" {
  type      = string
  sensitive = true
}

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
  name                   = "${var.prefix}-pg"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = var.location
  version                = "16"
  administrator_login    = "datapilot"
  administrator_password = var.pg_admin_password
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
}

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "${var.prefix}-logs"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  retention_in_days   = 30
}

resource "azurerm_application_insights" "ai" {
  name                = "${var.prefix}-appi"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  workspace_id        = azurerm_log_analytics_workspace.logs.id
  application_type    = "web"
}

resource "azurerm_cognitive_account" "openai" {
  name                = "${var.prefix}-openai"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  kind                = "OpenAI"
  sku_name            = "S0"
  # Model deployments are created separately; verify regional model availability.
}

output "storage_account_name" {
  value = azurerm_storage_account.lake.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.meta.fqdn
}

output "applicationinsights_connection_string" {
  value     = azurerm_application_insights.ai.connection_string
  sensitive = true # put into APPLICATIONINSIGHTS_CONNECTION_STRING of the API (ideally via Key Vault)
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}
