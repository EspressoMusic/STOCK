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

    # Telegram alerts, forex pairs only for now: breakout + 3 touches on the 1h chart
    # (app/retest_alerts.py), checked every hour on the 1h candle closes.
    # Token from @BotFather; chat id via `python -m app.telegram`. Either blank = no alerts.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Optional second bot just for the 1h alerts ("אסטרטגיה 1"), so they come in their own
    # Telegram chat; blank = the main bot sends them too. Same chat id — press Start in it once.
    telegram_1h_bot_token: str = ""
    # Third bot, for the 4h zone alerts on stocks; blank = no stock alerts. Same chat id too.
    telegram_stocks_bot_token: str = ""
    # Fourth bot, for Nasdaq 100 index scalping alerts; blank = off. Same chat id too.
    telegram_nasdaq_bot_token: str = ""
    # Fifth bot — penny stocks until 29/10/26, now the 15m forex drop-and-return alerts
    # (app/drop_return_scan.py); the name stays so .env does. Blank = off. Same chat id too.
    telegram_penny_bot_token: str = ""
    # Each bot's chat, if not the main one — e.g. a Telegram channel the bot posts in (its
    # @username or -100... id, with the bot an admin there). Blank = telegram_chat_id.
    telegram_1h_chat_id: str = ""
    telegram_stocks_chat_id: str = ""
    telegram_nasdaq_chat_id: str = ""
    telegram_penny_chat_id: str = ""
    # The stocks' 4h alerts, types 1 and 2 (app/tight_touch_scan.py): after each of the
    # session's two 4h candles. The pairs' 4h alerts run on their own 4h closes.
    stock_tight_touch_alert_times: str = "13:35,16:05"
    # Every US stock that trades, penny stocks and OTC included (the user asked for as many
    # as possible): ~6500 names with this volume, one paced Yahoo request each — ~25 minutes.
    stock_zone_touch_universe_size: int = 10_000
    stock_zone_touch_min_volume: int = 20_000
    # Candle times in the alert messages.
    alert_timezone: str = "Asia/Jerusalem"
    # The scanner page the alerts link to, which opens the alert's chart with its marking
    # (frontend AlertChartModal). The live site, so the link opens on the phone too; for the
    # local one use 127.0.0.1, not localhost: Telegram silently drops links to "localhost".
    alert_chart_url: str = "https://stock-one-flax.vercel.app/"

    # MetaTrader 5 (app/mt5_trader.py): trades on the terminal logged in on this computer.
    # "off" | "paper" (only logged) | "trade" (demo only, unless mt5_allow_real).
    mt5_mode: str = "off"
    mt5_allow_real: bool = False
    mt5_terminal_path: str = ""  # blank = the installed one
    # The strategy it trades on its own (app/ema_touch3_5m.py), on these symbols.
    mt5_strategy_symbols: str = "XAUUSD,EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,NAS100"
    # telegram.bot_chat names whose forex alerts are traded too, e.g. "main,1h"; blank = none.
    mt5_bots: str = ""
    mt5_risk_percent: float = 1.0  # of the balance, lost at the stop
    mt5_symbol_suffix: str = ""  # the broker's, e.g. ".m" for EURUSD.m; blank = found

    # Storage
    database_url: str = "sqlite:///./stockbot.db"

    # CORS — comma-separated list; a couple of common Vite dev ports are
    # allowed by default since another local project may already hold 5173.
    frontend_origins: str = "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174"

    @property
    def frontend_origin_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]


settings = Settings()
