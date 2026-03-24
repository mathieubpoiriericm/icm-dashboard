"""Add pipeline_runs table to store per-run statistics.

Tracks papers processed, full-text retrieval, genes extracted/validated,
and run mode for each pipeline execution. The Shiny dashboard reads the
latest row to display pipeline status in the About tab.

Revision ID: 003
Revises: 002
Create Date: 2026-03-24
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pipeline_runs (
            id SERIAL PRIMARY KEY,
            run_timestamp TIMESTAMPTZ NOT NULL,
            papers_processed INTEGER NOT NULL DEFAULT 0,
            fulltext_retrieved INTEGER NOT NULL DEFAULT 0,
            genes_extracted INTEGER NOT NULL DEFAULT 0,
            genes_validated INTEGER NOT NULL DEFAULT 0,
            run_mode TEXT NOT NULL DEFAULT 'standard'
        )
    """)
    op.execute(
        "CREATE INDEX idx_pipeline_runs_timestamp "
        "ON pipeline_runs(run_timestamp DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pipeline_runs_timestamp")
    op.execute("DROP TABLE IF EXISTS pipeline_runs")
