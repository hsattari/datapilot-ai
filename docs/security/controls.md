# Security controls: implemented, tested, and outstanding

## Implemented and tested (automated tests; 136 pass on PostgreSQL)
- Tenant isolation: tenant resolved server-side from membership; cross-tenant IDs return 404; per-tenant analytical store.
- Sign-in: bcrypt hashing (72-byte limit enforced), generic "Invalid credentials" for unknown email and wrong password
  (equal timing via a dummy hash), **lockout** after 5 failures for 15 minutes (also refuses a correct password
  during lockout), counters reset on success, every failure/lock/login audited.
- **Two-step sign-in (TOTP, RFC 6238 vectors tested):** setup, enable with a valid code, 8 single-use recovery codes
  (stored hashed), code replay refused, secret **encrypted at rest** with a key separate from the JWT secret,
  disable needs password plus a code.
- **User management:** admins add people (initial password), change roles, deactivate/reactivate; deactivation takes
  effect on the next request; the last active administrator cannot be removed; viewers are read-only.
- **Organisation policy "require two-step sign-in":** an admin can require it for everyone. People without it can reach only the set-up screens (`/auth/me`, `/auth/mfa/setup`, `/auth/mfa/enable`) and get `403 mfa_enrollment_required` everywhere else, on every request, so it applies to sessions already open. The admin must have two-step on before switching the policy on (no self-lockout); nobody under the policy can switch their own off; an admin can reset a colleague's two-step (lost phone), which is logged as `user.mfa_reset`. Scoped per organisation. Tested: 9 API tests and a browser flow.
- **Rate limiting:** per IP on register/login/MFA/password endpoints, per user on questions and uploads; 429 with
  Retry-After.
- **Production guard:** with ENVIRONMENT=production the API refuses to start with a default/short JWT secret, no MFA
  encryption key, or rate limiting off.
- Response headers: nosniff, frame deny, no-referrer, `Cache-Control: no-store` and a restrictive CSP on API responses.
- Uploads: extension/signature/size/binary checks, macro rejection, filename sanitising, path-traversal guard, immutable
  raw objects. Queries: structured plan -> template SQL with bound parameters -> sqlglot validator (single SELECT,
  allowlisted tables/functions, LEFT JOIN on key equality only) -> read-only per-tenant connection with timeout.
- **OIDC/Entra token exchange (API only):** strict RS256 verification, key caching/rotation, nonce, tenant id, provisioned
  users only, subject-based matching, no e-mail re-linking; tested with a fake identity provider incl. alg=none and
  HMAC-key-confusion attacks. See `oidc-entra.md`.
- **Malware scanning of uploads (ClamAV, fail-closed):** every upload is streamed to clamd (INSTREAM) after the cheap checks;
  infected files are rejected (422) and never stored; an unreachable scanner, timeout or error refuses the upload (503);
  `MALWARE_SCAN_FAIL_OPEN` exists only as an audited exception and production refuses it. Tested against a fake clamd
  (framing, chunking, errors, timeouts, mutation-checked); **not yet tested against a real ClamAV daemon**.
- Audit events for sign-in, user changes, approvals, queries, reports, alerts and settings (admins can read them).
- **Data lifecycle:** organisation export (ZIP with manifest, formula-safe CSV, no secrets), organisation deletion (name + password + MFA step-up, external stores first, proof-of-deletion record), person erasure, scheduled retention purge. See `data-lifecycle.md` for the limits (backups, unverified ADLS/Databricks paths).

## Known limits of what is built (be aware, not hidden)
- The rate limiter is in-process: with several API replicas each counts separately. Enforce limits at the gateway too.
- Lockout can be used to lock a known user out for 15 minutes (a trade-off for stopping password guessing).
- Access tokens last 30 minutes and are not revoked on password change; deactivation IS immediate (checked per request).
- Initial passwords for added users are set by an admin, not by an e-mailed invitation (no e-mail service yet).
- MFA is optional per user unless an administrator switches on the organisation policy (below). Accounts linked to a company identity provider are exempt from the policy and rely on the provider's own verification (not tested against a real provider).
- OIDC/Entra is **not tested against a real identity provider** and the web app has no Microsoft sign-in button yet.

## Outstanding before launch
Real-IdP test of OIDC/Entra and a Microsoft sign-in button, tenant-level MFA policy, e-mailed invitations, parser sandboxing/egress limits,
field-level restrictions, secrets in Key Vault, TLS termination, key-management review, backup/soft-delete configuration in Azure so erasure reaches backups, a real-service test of ADLS/Databricks deletion,
DPIA and legal review (UK GDPR / DPA 2018), independent penetration test and adversarial tenant-isolation testing.

## Outbound fetches (web address connector)

The server fetches addresses that customers enter, so `services/netguard.py` applies SSRF rules on every fetch and on every redirect hop: https on port 443 only; no credentials in the address; local-style host names refused; every resolved address must be public; the connection is made to the address that was checked (so DNS rebinding cannot swap it); proxy environment variables are ignored; the decoded body is size-capped and the fetch time-limited. Query strings (which may hold pre-signed tokens) are not written to audit events or HTTP logs. Residual risk: tested with mocked DNS and HTTP only; add network-level egress filtering when private networking is introduced. Credentials for sources are not supported until a per-organisation secret store is designed.
