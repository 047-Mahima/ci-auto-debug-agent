#stores configuration settings for the application
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "ci-cd-auto-debug-agent"
    app_env: str = "dev"
    database_url: str = "sqlite:///./ci_debug_agent.db"
    github_webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
