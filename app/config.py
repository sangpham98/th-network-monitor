import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
ENV_FILE = Path(os.environ.get("THNM_ENV_FILE", BASE_DIR / ".env"))


class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    database_url: str = "sqlite:///./data/network_monitor.db"
    monitor_interval_seconds: int = 30
    ping_timeout_seconds: int = 2
    ping_retry: int = 3
    down_threshold: int = 4
    up_threshold: int = 2
    max_concurrency: int = 100
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_reminder_interval_seconds: int = 21600
    timezone: str = DEFAULT_TIMEZONE
    log_level: str = "INFO"
    data_dir: Path = BASE_DIR / "data"
    log_dir: Path = BASE_DIR / "logs"
    auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password: str = ""
    session_secret: str = "change-me"
    session_cookie_name: str = "thnm_session"
    session_max_age_seconds: int = 28800

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")


settings = Settings()
