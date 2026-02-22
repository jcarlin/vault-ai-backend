from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # vLLM backend
    vllm_base_url: str = "http://localhost:8001"
    vllm_api_key: str | None = None
    vllm_api_prefix: str = "/v1"

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

    # Cloud deployment
    vault_access_key: str | None = None  # Shared secret gate (None = disabled, for Cube)
    vault_deployment_mode: str = "cube"  # "cube" or "cloud"
    vault_admin_api_key: str | None = None  # Deterministic admin key for cloud (vault_sk_<48 hex>)

    # Model management
    vault_models_dir: str = "/opt/vault/models"
    vault_vllm_container_name: str = "vault-vllm"
    vault_gpu_config_path: str = "config/gpu-config.yaml"

    # HTTP client timeouts (seconds)
    vault_http_connect_timeout: float = 5.0
    vault_http_read_timeout: float = 120.0

    # Backup/restore
    vault_backup_dir: str = "/opt/vault/backups"

    # Quarantine pipeline
    vault_quarantine_dir: str = "/opt/vault/quarantine"
    vault_clamav_socket: str = "/var/run/clamav/clamd.ctl"
    vault_yara_rules_dir: str = "/opt/vault/quarantine/signatures/yara_rules"
    vault_blacklist_path: str = "/opt/vault/quarantine/blacklist.json"

    # LDAP / SSO
    vault_ldap_enabled: bool = False
    vault_ldap_url: str = "ldap://localhost:389"
    vault_ldap_bind_dn: str = ""
    vault_ldap_bind_password: str = ""
    vault_ldap_user_search_base: str = ""
    vault_ldap_group_search_base: str = ""
    vault_ldap_user_search_filter: str = "(sAMAccountName={username})"
    vault_ldap_use_ssl: bool = False

    # JWT
    vault_jwt_algorithm: str = "HS256"
    vault_jwt_expiry_seconds: int = 3600

    model_config = {"env_prefix": "", "case_sensitive": False, "env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
