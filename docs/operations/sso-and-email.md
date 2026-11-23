# Microsoft sign-in and invitation e-mail

Both are optional. With neither set up, people sign in with a password and invitations show the link to the administrator.

## Microsoft Entra sign-in
1. Entra admin centre > App registrations > New registration. Single tenant. Platform **Single-page application**, redirect URI `https://<your-web-address>/`.
2. Authentication > tick **ID tokens** (used for implicit and hybrid flows).
3. Note the Application (client) ID and Directory (tenant) ID.
4. Terraform variables: `oidc_issuer = "https://login.microsoftonline.com/<tenant-id>/v2.0"`, `oidc_client_id`, `oidc_required_tid = "<tenant-id>"`.
5. An administrator must first add or invite the person with the same e-mail; the first Microsoft sign-in links the account. Nobody is created automatically. Two-step verification is then Entra's job.

## Invitation e-mail (SMTP)
Any SMTP server works. For Azure Communication Services Email: create the service and a domain, then an SMTP username in Communication Services > SMTP usernames.
Variables: `smtp_host`, `smtp_username`, `smtp_from`, `app_base_url = "https://<your-web-address>"`; the password goes in `TF_VAR_smtp_password` (stored in Key Vault).
Test: Settings > People > Invite a person. If the page says "No mail server is set up", the host or sender is empty or the send failed (check the API log; message text is never logged).
