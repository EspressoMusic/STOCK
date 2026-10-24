from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Paddle billing — gates every AI feature behind a Pro subscription so the
    # free tier can never generate OpenAI cost. Plan state lives in the DB
    # (AppLicense row, flipped by the webhook); these are just Paddle's own
    # identifiers. Leave all blank to keep the app permanently free/AI-off.
    paddle_webhook_secret: str = ""
    paddle_client_token: str = ""
    paddle_price_id: str = ""
    paddle_environment: str = "sandbox"  # "sandbox" | "production"
    # Lets you flip the plan locally without a real Paddle webhook, for testing.
    # Leave blank in production — anyone holding this token can turn AI on for free.
    paddle_dev_override_token: str = ""

    # Google sign-in — must match the frontend's VITE_GOOGLE_CLIENT_ID so the
    # backend can verify that an ID token was actually issued for this app.
    google_client_id: str = ""

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

    # Technical scan (15m StochRSI overbought/oversold + EMA50 touch) — on-demand only,
    # no scheduling, no persistence.
    technical_stochrsi_overbought: float = 80.0
    technical_stochrsi_oversold: float = 20.0
    technical_ema_period: int = 50
    technical_stock_universe_size: int = 250
    technical_scan_workers: int = 12
    # The triple-touch (repeated rejection) scan casts a much wider net than the other
    # scans: the top 1000 Nasdaq stocks by volume, which takes in plenty of small caps
    # (~half) and broken penny stocks (~1 in 6). The whole ~2300 traded names would take
    # ~8 minutes at the pace Yahoo tolerates; 1000 takes ~3.5.
    triple_touch_stock_universe_size: int = 1000
    triple_touch_min_stock_volume: int = 50_000
    # "Shilo 2" (Stoch RSI extreme on daily + 4h) costs one request per stock plus a second
    # for the ~2/3 stretched on the daily chart, so 500 stocks take ~2.5-3 minutes at the
    # paced rate (1000 would take ~5.5).
    stoch_extreme_stock_universe_size: int = 500
    stoch_extreme_min_stock_volume: int = 50_000

    # Telegram alerts: breakout + 3 retests on the daily chart (app/retest_alerts.py).
    # Token from @BotFather; chat id via `python -m app.telegram`. Either blank = no alerts.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    retest_alert_time: str = "16:30"  # Mon-Fri, scan_timezone — after the US close
    retest_alert_stock_universe_size: int = 1000
    retest_alert_min_stock_volume: int = 50_000
    # Stocks still inside a 3-touch 4h zone (app/zone_touch_scan.py), one Telegram list a day.
    zone_touch_alert_time: str = "16:45"  # Mon-Fri, scan_timezone — after the retest alerts
    # The scanner page the alerts link to, which opens the alert's chart with its marking
    # (frontend AlertChartModal). The live site, so the link opens on the phone too; for the
    # local one use 127.0.0.1, not localhost: Telegram silently drops links to "localhost".
    alert_chart_url: str = "https://stock-one-flax.vercel.app/"

    # Storage
    database_url: str = "sqlite:///./stockbot.db"

    # CORS — comma-separated list; a couple of common Vite dev ports are
    # allowed by default since another local project may already hold 5173.
    frontend_origins: str = "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174"

    @property
    def frontend_origin_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]


settings = Settings()
