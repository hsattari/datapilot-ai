# Starter-profile Azure infrastructure. NOT yet validated with `terraform validate/plan`;
# review SKUs, networking (private endpoints) and policies before use.
terraform {
  required_providers { azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" } }
}
provider "azurerm" { features {} }

variable "location"       { default = "uksouth" }
variable "prefix"         { default = "datapilot" }
variable "pg_admin_password" { sensitive = true }

resource "azurerm_resource_group" "rg" {
  name     = "${var.prefix}-rg"
  location = var.location
}

resource "azurerm_storage_account" "lake" {
  name                     = "${var.prefix}lake${substr(md5(azurerm_resource_group.rg.id), 0, 6)}"
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  is_hns_enabled           = true # ADLS Gen2
  min_tls_version          = "TLS1_2"
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
