# Environment: staging. Use with its own Terraform workspace so the state never mixes:
#   terraform workspace new staging   (or: terraform workspace select staging)
#   terraform apply -var-file=environments/staging.tfvars -var deploy_apps=true -var api_image_tag=<tag> -var web_image_tag=<tag>
# Secrets (jwt_secret, mfa_encryption_key, pg_admin_password, azure_openai_api_key) are NEVER put in these files:
# pass them as TF_VAR_* environment variables, with DIFFERENT values per environment.
prefix           = "dpstaging"
location         = "uksouth"
log_daily_cap_gb = 1
