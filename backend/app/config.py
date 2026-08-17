from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Scan defaults
    scan_min_abs_percent: float = 8.0
    scan_min_volume: int = 100_000
    scan_max_results: int = 100
    scan_exclude_derivatives: bool = True

    # "Broken stocks" watchlist defaults — penny stocks with a deep drawdown over
    # the trailing year, independent of today's move.
    broken_max_price: float = 2.0
    broken_min_drawdown_percent: float = 60.0
    broken_min_volume: int = 20_000

    # Scheduler (24h "HH:MM", US/Eastern)
    scan_time_1: str = "09:45"
    scan_time_2: str = "16:15"
    broken_scan_time: str = "08:30"
    scan_timezone: str = "America/New_York"

    # Storage
    database_url: str = "sqlite:///./stockbot.db"

    # CORS — comma-separated list; a couple of common Vite dev ports are
    # allowed by default since another local project may already hold 5173.
    frontend_origins: str = "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174"

    @property
    def frontend_origin_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]


settings = Settings()
