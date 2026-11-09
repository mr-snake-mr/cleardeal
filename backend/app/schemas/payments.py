"""Payment request and response shapes (docs/08 §5).

`amount` is never accepted on the way in. It is read from `milestones.amount`
inside the transaction, so there is nothing for a browser to tamper with.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.projects import MoneyStr


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paypal_order_id: str = Field(min_length=5, max_length=64)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    milestone_id: uuid.UUID
    amount: MoneyStr
    currency: str
    status: str
    paypal_order_id: str | None
    paypal_capture_id: str | None
    captured_at: datetime | None


class PaymentCreatedOut(PaymentOut):
    """Creation only: the browser needs the public client id and the URL to
    send the payer to. `paypal_client_id` is public by PayPal's design; the
    secret is the other half of the pair and never appears here."""

    payment_id: uuid.UUID
    paypal_client_id: str
    approve_url: str
