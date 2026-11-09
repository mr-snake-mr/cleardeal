"""change_requests: two signature timestamps

Revision ID: d8e5f3b02c41
Revises: c7d4e2a91b30
"""

from alembic import op
import sqlalchemy as sa

revision = "d8e5f3b02c41"
down_revision = "c7d4e2a91b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "change_requests",
        sa.Column("freelancer_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "change_requests",
        sa.Column("client_approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("change_requests", "client_approved_at")
    op.drop_column("change_requests", "freelancer_approved_at")
