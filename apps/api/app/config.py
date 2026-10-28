from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    # Operational metadata (PostgreSQL in production; SQLite allowed for tests)
    database_url: str = "postgresql+psycopg2://datapilot:datapilot@localhost:5432/datapilot"

    # "production" refuses to start with the default/short JWT secret
    environment: str = "development"

    # Identity
    jwt_secret: str = "change-me-in-production"
    mfa_encryption_key: str = ""   # separate key for MFA secrets at rest (falls back to jwt_secret; use Key Vault in production)
    login_max_failures: int = 5
    # Observability: JSON logs by default; traces go to Application Insights when a connection string is set
    log_format: str = "json"                 # "json" or "text" (text is easier to read while developing)
    applicationinsights_connection_string: str = ""
    otel_console: bool = False               # print spans to the console (debugging)
    # OIDC single sign-on (e.g. Microsoft Entra ID). The browser (MSAL, auth-code + PKCE) obtains an ID token and
    # the API verifies it. Issuer for Entra: https://login.microsoftonline.com/<tenant-id>/v2.0
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_jwks_uri: str = ""        # optional; discovered from the issuer when empty
    oidc_required_tid: str = ""    # optional Entra tenant id the token must come from
    login_lockout_minutes: int = 15

    # Rate limits (requests per minute; 0 = off). In-process: also enforce at the gateway in production.
    # Retention (days; 0 = keep forever). Expired question history and audit events are purged by the worker.
    retention_queries_days: int = 365
    retention_audit_days: int = 730
    rate_limit_enabled: bool = True
    rate_auth_per_minute: int = 20       # per client IP: register, login, MFA
    rate_query_per_minute: int = 30      # per user: questions
    rate_upload_per_minute: int = 10     # per user: file uploads
    rate_export_per_minute: int = 3      # per user: full data exports
    rate_answer_export_per_minute: int = 10   # per user: CSV export of one answer
    trust_proxy_headers: bool = False    # honour X-Forwarded-For only behind a proxy you control
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 30

    # Malware scanning of uploads: "off" or "clamav" (a clamd daemon, e.g. the clamav/clamav container).
    # Fail-closed: if scanning is on and the scanner cannot answer, uploads are refused (503).
    malware_scan: str = "off"
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 15
    malware_scan_fail_open: bool = False   # only for an explicit, audited decision; never in production

    # Raw storage: "local" for dev, "adls" for Azure Data Lake Storage Gen2
    storage_backend: str = "local"
    local_storage_path: str = "./.data/raw"
    adls_account_url: str = ""
    adls_filesystem: str = "raw"

    # Analytical store (Starter profile uses DuckDB)
    analytics_path: str = "./.data/analytics"

    # Analytics engine: "duckdb" (Starter) or "databricks" (Scale; Unity Catalog)
    analytics_backend: str = "duckdb"
    databricks_server_hostname: str = ""
    databricks_http_path: str = ""
    databricks_token: str = ""            # write principal (ingestion/modelling)
    databricks_readonly_token: str = ""   # SELECT-only principal for the query path (recommended)
    databricks_catalog: str = ""

    # New organisations build models and approve the built-in metrics automatically when columns are recognised.
    auto_model_default: bool = True

    # Scheduled alert checks (minutes between checks of each alert; the worker runs them). 0 = off.
    alert_check_minutes: int = 60

    # Plan given to new organisations (starter | growth | advanced | enterprise). Limits: app/services/quotas.py.
    default_plan: str = "starter"

    # Upload limits
    max_upload_mb: int = 25
    connector_timeout_seconds: float = 20.0
    connector_allow_http: bool = False     # development only: allows http:// on port 80; hosts are still checked
    rate_sync_per_minute: int = 6          # per user: manual URL syncs
    max_sheet_rows: int = 1_000_000

    # Query safety
    query_row_limit: int = 10_000
    query_timeout_seconds: int = 15

    # Azure OpenAI (optional; the planner falls back to a rule-based mode)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    embedding_dimensions: int = 1536  # text-embedding-3-large supports shortened vectors; fits pgvector indexes
    azure_openai_api_version: str = "2024-10-21"


@lru_cache
def get_settings() -> Settings:
    return Settings()
