"""Add functional index for UPPER(gene) to avoid sequential scans.

The UPDATE in merge_genes_transactional() uses WHERE UPPER(gene) = UPPER($4),
which bypasses the existing btree index on genes(gene). This migration adds
a functional index on UPPER(gene) to support case-insensitive lookups.

Revision ID: 002
Revises: 001
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_genes_gene_upper ON genes(UPPER(gene))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_genes_gene_upper")
