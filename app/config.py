from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8080
    log_level: str = "INFO"

    auth_config_path: str = "./auth.yaml"
    security_config_path: str = "./security.yaml"
    trust_proxy_headers: bool = False

    backend_api_base: str
    backend_api_key: str
    backend_model: str = "gpt-4o"


@lru_cache
def get_settings() -> Settings:
    return Settings()
