# Environment: prod. Use with its own Terraform workspace so the state never mixes:
#   terraform workspace new prod   (or: terraform workspace select prod)
#   terraform apply -var-file=environments/prod.tfvars -var deploy_apps=true -var api_image_tag=<tag> -var web_image_tag=<tag>
# Secrets (jwt_secret, mfa_encryption_key, pg_admin_password, azure_openai_api_key) are NEVER put in these files:
# pass them as TF_VAR_* environment variables, with DIFFERENT values per environment.
prefix           = "datapilot"
location         = "uksouth"
log_daily_cap_gb = 2
