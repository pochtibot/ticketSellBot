"""Add VK Pay orders for organizer subscriptions and event premium.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE subscriptiontier AS ENUM ('basic', 'pro'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.create_table(
        "vk_pay_product_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_user_id", sa.String(length=255), nullable=False),
        sa.Column("product_type", sa.String(length=32), nullable=False),
        sa.Column(
            "tier",
            postgresql.ENUM("basic", "pro", name="subscriptiontier", create_type=False),
            nullable=True,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(product_type = 'subscription' AND tier IS NOT NULL AND event_id IS NULL) OR "
            "(product_type = 'event_premium' AND tier IS NULL AND event_id IS NOT NULL)",
            name="ck_vk_pay_product_order_target",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer_id"),
        sa.UniqueConstraint("provider_transaction_id"),
    )
    op.create_index("ix_vk_pay_product_orders_user_id", "vk_pay_product_orders", ["user_id"])
    op.create_index("ix_vk_pay_product_orders_event_id", "vk_pay_product_orders", ["event_id"])
    op.create_index("ix_vk_pay_product_orders_expires_at", "vk_pay_product_orders", ["expires_at"])
    op.create_index(
        "ix_vk_pay_product_user_status_expires",
        "vk_pay_product_orders",
        ["user_id", "status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vk_pay_product_user_status_expires", table_name="vk_pay_product_orders")
    op.drop_index("ix_vk_pay_product_orders_expires_at", table_name="vk_pay_product_orders")
    op.drop_index("ix_vk_pay_product_orders_event_id", table_name="vk_pay_product_orders")
    op.drop_index("ix_vk_pay_product_orders_user_id", table_name="vk_pay_product_orders")
    op.drop_table("vk_pay_product_orders")
