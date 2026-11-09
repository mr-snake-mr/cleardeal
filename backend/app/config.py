"""Application settings, loaded from the environment.

No secret is declared here yet — PayPal credentials arrive with the PayPal client
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

    # Constrained so a typo cannot silently produce a third environment.
    paypal_env: Literal["sandbox", "live"] = "sandbox"


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read once per process."""
    return Settings()


settings = get_settings()
