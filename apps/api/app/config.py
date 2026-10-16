from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Operational metadata (PostgreSQL in production; SQLite allowed for tests)
    database_url: str = "postgresql+psycopg2://datapilot:datapilot@localhost:5432/datapilot"

    # Identity
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 30

    # Raw storage: "local" for dev, "adls" for Azure Data Lake Storage Gen2
    storage_backend: str = "local"
    local_storage_path: str = "./.data/raw"
    adls_account_url: str = ""
    adls_filesystem: str = "raw"

    # Analytical store (Starter profile uses DuckDB)
    analytics_path: str = "./.data/analytics"

    # Upload limits
    max_upload_mb: int = 25
    max_sheet_rows: int = 1_000_000

    # Query safety
    query_row_limit: int = 10_000
    query_timeout_seconds: int = 15

    # Azure OpenAI (optional; the planner falls back to a rule-based mode)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"


@lru_cache
def get_settings() -> Settings:
    return Settings()
