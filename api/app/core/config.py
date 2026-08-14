"""Configuración central de la aplicación.

Todos los valores llegan por variables de entorno (EasyPanel en producción,
docker-compose o .env en local). Nunca hardcodear secretos.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "local"
    port: int = 80
    root_path: str = ""
    log_level: str = "INFO"

    # Base de datos
    database_url: str = "postgresql+psycopg://colombia_unida:colombia_unida@localhost:5432/colombia_unida"

    # Seguridad
    app_encryption_key: str = ""
    app_hmac_key: str = ""
    audit_signing_key: str = ""
    jwt_secret: str = ""

    # Meta WhatsApp Cloud API
    meta_graph_api_version: str = "v23.0"
    meta_waba_id: str = ""
    meta_phone_number_id: str = ""
    meta_access_token: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = ""

    # IA
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    stt_provider: str = "disabled"
    openai_api_key: str = ""

    # Objetos S3/MinIO
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_quarantine: str = "quarantine"
    s3_bucket_protected: str = "protected"
    s3_bucket_public: str = "public"
    s3_bucket_audit: str = "audit"

    # ClamAV
    clamav_host: str = ""
    clamav_port: int = 3310

    # n8n
    n8n_webhook_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
