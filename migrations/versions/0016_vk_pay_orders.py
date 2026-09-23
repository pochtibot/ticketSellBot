"""Add VK Pay reservations and provider references.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("payments", sa.Column("provider_order_id", sa.String(length=128), nullable=True))
    op.add_column("payments", sa.Column("provider_transaction_id", sa.String(length=128), nullable=True))
    op.create_index("ix_payments_provider_order_id", "payments", ["provider_order_id"], unique=True)
    op.create_index("ix_payments_provider_transaction_id", "payments", ["provider_transaction_id"], unique=True)

    op.create_table(
        "vk_pay_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer_id", sa.String(length=128), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_user_id", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("base_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(precision=10, scale=2), server_default="0", nullable=False),
        sa.Column("promo_code_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("promo_code", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer_id"),
        sa.UniqueConstraint("provider_transaction_id"),
    )
    op.create_index("ix_vk_pay_orders_event_id", "vk_pay_orders", ["event_id"])
    op.create_index("ix_vk_pay_orders_user_id", "vk_pay_orders", ["user_id"])
    op.create_index("ix_vk_pay_orders_expires_at", "vk_pay_orders", ["expires_at"])
    op.create_index(
        "ix_vk_pay_order_event_status_expires", "vk_pay_orders", ["event_id", "status", "expires_at"]
    )
    op.create_index(
        "ix_vk_pay_order_promo_status_expires", "vk_pay_orders", ["promo_code_id", "status", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_vk_pay_order_promo_status_expires", table_name="vk_pay_orders")
    op.drop_index("ix_vk_pay_order_event_status_expires", table_name="vk_pay_orders")
    op.drop_index("ix_vk_pay_orders_expires_at", table_name="vk_pay_orders")
    op.drop_index("ix_vk_pay_orders_user_id", table_name="vk_pay_orders")
    op.drop_index("ix_vk_pay_orders_event_id", table_name="vk_pay_orders")
    op.drop_table("vk_pay_orders")
    op.drop_index("ix_payments_provider_transaction_id", table_name="payments")
    op.drop_index("ix_payments_provider_order_id", table_name="payments")
    op.drop_column("payments", "provider_transaction_id")
    op.drop_column("payments", "provider_order_id")
    op.drop_column("payments", "provider")
