"""Application settings, loaded from the environment.

Secrets arrive from the environment only. PayPal credentials are per-tenant and
and are modelled as required fields at that point, so a missing value fails at
startup rather than at the first payment. `extra="ignore"` means a variable
present in .env but undeclared here is silently dropped, so anything required
must be declared.

`paypal_api_base` is deliberately absent: it has no reader until the PayPal
client exists, and a base URL that can disagree with `paypal_env` is worse than
none.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ClearDeal"
    app_version: str = "0.1.0"
    debug: bool = False

    database_url: str = "sqlite:///./cleardeal.db"

    # JSON array in the environment, e.g. ["http://localhost:5173"]
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # The origin links are built against. Empty means "use the request's own
    # base URL", which is right behind a tunnel and in tests. Set it on a host
    # whose proxy is not trusted to forward the scheme (see docs/08).
    public_base_url: str = ""

    # Constrained so a typo cannot silently produce a third environment.
    paypal_env: Literal["sandbox", "live"] = "sandbox"
    # The webhook id PayPal issues for this app. Empty means nothing verifies.
    paypal_webhook_id: str = ""

    # Signs session cookies. The default exists so `pytest` and a fresh clone
    # boot without ceremony; `create_app` refuses to start with it in `live`
    # mode, because a published signing key is an authentication bypass.
    session_secret: str = "dev-insecure-secret-change-me"
    session_ttl_hours: int = 12

    # Gemini. Absent key = AI endpoints return 503 and the payment path is
    # unaffected; it never blocks the demo (docs/05 §9).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # 30, not 10. A flash model under load routinely needs more than ten
    # seconds, and the client is specified to give up after exactly one retry
    # (05 §5). At 10s that rule turned a working provider into `503
    # AI_UNAVAILABLE`: on 2026-11-10 four consecutive attempts recorded
    # `transport_error` while the fifth returned a good answer. The timeout is
    # a budget for the provider, not a promise about latency — the UI shows a
    # spinner either way.
    ai_timeout_seconds: float = 30.0

    # Model B only. Absent key = credential storage refuses to run rather than
    # writing plaintext (app/crypto.py). Deliberately not defaulted to a value:
    # a default encryption key is a published encryption key.
    credentials_encryption_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read once per process."""
    return Settings()


settings = get_settings()
