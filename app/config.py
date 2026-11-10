"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Security
    SECRET_KEY: str = "dev-secret-change-me"

    # Panel
    PANEL_PORT: int = 8080
    PANEL_PATH: str = "/"

    # Default admin (only used on first bootstrap if no admin exists)
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str | None = None

    # Database
    DATABASE_URL: str = "sqlite:///./xray_panel.db"

    # Xray
    XRAY_BIN_PATH: str = "/usr/local/bin/xray"   # used to validate configs
    XRAY_CONFIG_PATH: str = "/usr/local/etc/xray/config.json"
    # systemd unit driving xray on the server (Phase 5: start/stop/restart)
    XRAY_SERVICE_NAME: str = "xray"

    # Xray stats API (Phase 4): clients traffic counters + auto-disable.
    XRAY_STATS_ENABLED: bool = True
    XRAY_API_HOST: str = "127.0.0.1"
    XRAY_API_PORT: int = 23231

    # Optional background traffic sweep; 0 disables the loop (manual refresh only).
    MONITOR_INTERVAL_SECONDS: int = 0

    # Telegram bot (Phase 6, optional): CLI-run via `python -m app.telegram_bot`.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
