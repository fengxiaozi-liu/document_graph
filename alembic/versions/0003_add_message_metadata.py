"""add messages.metadata for refs replay

Revision ID: 0003_add_message_metadata
Revises: 0002_backfill_docver_types
Create Date: 2026-01-15

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_add_message_metadata"
down_revision = "0002_backfill_docver_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "metadata")

