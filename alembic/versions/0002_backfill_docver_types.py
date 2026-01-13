"""backfill document_versions file_ext and mime_type

Revision ID: 0002_backfill_docver_types
Revises: 0001_initial
Create Date: 2026-01-14

"""
from __future__ import annotations

from alembic import op


revision = "0002_backfill_docver_types"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE document_versions
        SET file_ext = lower(substring(storage_uri from '\.([^.]+)$'))
        WHERE file_ext IS NULL
          AND storage_uri ~ '\.[^.]+$';
        """
    )
    op.execute(
        r"""
        UPDATE document_versions
        SET mime_type = CASE file_ext
            WHEN 'pdf' THEN 'application/pdf'
            WHEN 'docx' THEN 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            WHEN 'doc' THEN 'application/msword'
            WHEN 'md' THEN 'text/markdown'
            WHEN 'html' THEN 'text/html'
            WHEN 'htm' THEN 'text/html'
            WHEN 'txt' THEN 'text/plain'
            ELSE mime_type
        END
        WHERE mime_type IS NULL
          AND file_ext IS NOT NULL;
        """
    )


def downgrade() -> None:
    pass
