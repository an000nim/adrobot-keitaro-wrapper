from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    keitaro_url: str = "https://your-tracker.example"
    keitaro_api_key: SecretStr = SecretStr("")
    database_url: str = "sqlite:///./adrobot.db"
    campaign_prefix: str = "test_tyutikov_"
    report_timezone: str = "Europe/Moscow"

    @field_validator("keitaro_url")
    @classmethod
    def validate_url(cls, value):
        value = value.rstrip("/")
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError("KEITARO_URL должен быть HTTPS-адресом трекера без /admin")
        return value

    @field_validator("campaign_prefix")
    @classmethod
    def validate_prefix(cls, value):
        if not value.startswith("test_") or not value.endswith("_") or len(value) < 7:
            raise ValueError("CAMPAIGN_PREFIX должен иметь вид test_фамилия_")
        return value

    @field_validator("report_timezone")
    @classmethod
    def validate_timezone(cls, value):
        ZoneInfo(value)
        return value


settings = Settings()
