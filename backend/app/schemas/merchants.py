"""Merchant credential shapes.

There is no response field for a secret. Not masked, not truncated — absent.
A masked secret still leaks its length and prefix, and a response schema that
*can* carry one will eventually carry one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MerchantConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # No upper bound tight enough to reject a real PayPal secret; the floor
    # rejects an obvious placeholder like "x".
    client_id: str = Field(min_length=10, max_length=200)
    client_secret: str = Field(min_length=10, max_length=200)


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    paypal_merchant_id: str | None
    connected_at: datetime
