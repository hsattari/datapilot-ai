# Security controls implemented / outstanding

Implemented: server-side tenant resolution; 404 (not 403) on cross-tenant IDs; upload extension/signature/
size/binary checks and macro rejection; filename sanitising; path-traversal guard in storage; immutable raw
objects; parameterised SQL; SQL validator; read-only per-tenant analytical connections; audit events;
bcrypt password hashing; short-lived JWTs.

Outstanding before launch: OIDC + MFA, rate limiting, malware scanning, parser sandboxing/egress limits,
field-level restrictions, secrets in Key Vault, TLS termination, encryption/key management review,
retention/deletion workflows, DPIA and legal review (UK GDPR / DPA 2018), adversarial tenant-isolation testing.
