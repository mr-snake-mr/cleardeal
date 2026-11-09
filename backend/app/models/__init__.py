"""Model registry.

Importing this module registers every table on `Base.metadata`, which is what
Alembic reads.
"""

from app.models.agreements import (
    Agreement,
    AgreementSection,
    AgreementVersion,
    ChangeItem,
    ChangeRequest,
    Milestone,
)
from app.models.commerce import MerchantAccount, Payment
from app.models.identity import Invitation, User
from app.models.projects import Project
from app.models.system import AiRun, AuditEvent, WebhookEvent

__all__ = [
    "Agreement",
    "AgreementSection",
    "AgreementVersion",
    "AiRun",
    "AuditEvent",
    "ChangeItem",
    "ChangeRequest",
    "Invitation",
    "MerchantAccount",
    "Milestone",
    "Payment",
    "Project",
    "User",
    "WebhookEvent",
]
