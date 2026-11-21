# Environments and promotion

Four environments, each its own Azure resource group built from the same Terraform and its own Terraform **workspace**
(its own state), so nothing can leak between them:

| Environment | Terraform workspace | tfvars | Purpose |
|---|---|---|---|
| dev | `dev` | `environments/dev.tfvars` | Trying changes; may be destroyed any time |
| test | `test` | `environments/test.tfvars` | Automated and manual testing with synthetic data only |
| staging | `staging` | `environments/staging.tfvars` | Production-like; the release candidate runs the smoke test here |
| prod | `default` (the stack you already have) | `environments/prod.tfvars` | Real customers |

Rules
- Never put real customer data in dev, test or staging. Use `data/synthetic`.
- Secrets differ per environment and are passed as `TF_VAR_*` variables, never written into a tfvars file or the repo.
- Create an environment: `terraform workspace new staging`, then
  `terraform apply -var-file=environments/staging.tfvars` (data layer first, as in `azure-starter-deploy.md`).
  Check `terraform workspace show` before every apply. Applying prod values in the wrong workspace is the one mistake to avoid.
- Your existing deployment is the `default` workspace and uses the prefix `datapilot`; leave it as prod.

## Promotion (build once, promote the same image)
1. CI (tests, dbt, PostgreSQL run, image scan) is green on `main`.
2. Run **publish-images** with a new tag, e.g. `v12`. The same image is used in every environment.
3. Apply **staging** with `api_image_tag=v12 web_image_tag=v12`.
4. Run the **smoke-test** workflow (Actions) with the staging web address. It signs up a throwaway organisation,
   uploads the reference file, checks net revenue 350 and gross profit 140, and deletes the organisation.
5. Only if it passes, apply **prod** with the same tag, then run the smoke test against prod.
6. If prod misbehaves, roll back (`rollback.md`).

A staging environment costs money while it exists. Scale it down or destroy it between releases
(`terraform destroy` in the staging workspace only; confirm the workspace name first).
