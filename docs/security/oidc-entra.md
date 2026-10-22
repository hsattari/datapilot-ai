# Single sign-on with Microsoft Entra ID (OIDC)

Status: the API side is built and tested against a **fake identity provider** (locally generated keys). It has **not**
been tested against a real Entra tenant, and the web app has **no "Sign in with Microsoft" button yet** (it needs an app
registration and the MSAL browser library, see step 4).

## How it works
1. The browser signs the person in at Entra with the authorization-code + PKCE flow (MSAL does this) and gets an **ID token**.
2. The browser sends it to `POST /api/v1/auth/oidc/exchange` (with the `nonce` MSAL used).
3. The API verifies the token strictly: RS256 only, signature against Entra's published keys (cached; refreshed once a
   minute at most when an unknown key id appears), issuer, audience (your client id), expiry, required claims, optional
   tenant id (`tid`) and the nonce. Any failure is a generic 401.
4. Only people an **administrator has already added** (People screen) can sign in; nobody is created automatically. The
   first sign-in links the Entra identity to the account by e-mail; afterwards the account is matched only by
   (issuer, subject), so a changed or spoofed e-mail claim cannot take over an account, and an account linked to one
   identity cannot be re-linked to another by e-mail.
5. Deactivating a person in the People screen stops SSO immediately (checked on every request).
6. Multi-factor on this path is Entra's job (use Conditional Access). The local authenticator-app MFA applies to password sign-in.

## Set-up checklist (needs an Entra tenant you administer)
1. Entra admin centre > App registrations > New registration. Platform: **Single-page application**, redirect URI
   `http://localhost:3000` (add your production URL later). Single tenant.
2. Note the **Application (client) ID** and **Directory (tenant) ID**.
3. Set in `.env`:
   ```
   OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
   OIDC_CLIENT_ID=<application-client-id>
   OIDC_REQUIRED_TID=<tenant-id>
   ```
4. Front end: `npm install @azure/msal-browser`, configure it with `GET /api/v1/auth/oidc/config` (authority =
   issuer, clientId), call `loginPopup({scopes:["openid","profile","email"]})`, send `idToken` and the nonce to the
   exchange endpoint, and use the returned token like a normal sign-in token. (Not built yet: tell me when the
   registration exists and I will build and test it.)
5. In the Entra enterprise application set "Assignment required" so only chosen people can obtain tokens.

## Security notes
- Do not enable a multi-tenant issuer (`/common`): token issuers differ per tenant and the `email` claim is not proof of ownership.
- Entra does not send `email_verified`; that is why accounts are pre-provisioned by an administrator and linked once.
