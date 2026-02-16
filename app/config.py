from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # vLLM backend
    vllm_base_url: str = "http://localhost:8001"
    vllm_api_key: str | None = None

    # Security
    vault_secret_key: str = "dev-secret-key-change-in-production"

    # Database
    vault_db_url: str = "sqlite+aiosqlite:///data/vault.db"

    # Logging
    vault_log_level: str = "info"

    # Model manifest
    vault_models_manifest: str = "config/models.json"

    # CORS
    vault_cors_origins: str = "https://vault-cube.local"

    # Setup wizard
    vault_setup_flag_path: str = "/opt/vault/data/.setup_complete"
    vault_tls_cert_dir: str = "/opt/vault/tls"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
