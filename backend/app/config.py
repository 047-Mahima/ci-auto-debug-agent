#stores configuration settings for the application
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "ci-cd-auto-debug-agent"
    app_env: str = "dev"
    database_url: str = "sqlite:///./ci_debug_agent.db"
    github_webhook_secret: str = ""
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    review_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
