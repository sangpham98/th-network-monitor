from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    database_url: str = "sqlite:///./data/network_monitor.db"
    monitor_interval_seconds: int = 60
    ping_timeout_seconds: int = 1
    ping_retry: int = 2
    down_threshold: int = 3
    up_threshold: int = 2
    max_concurrency: int = 150
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    timezone: str = "Asia/Ho_Chi_Minh"

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8")


settings = Settings()
