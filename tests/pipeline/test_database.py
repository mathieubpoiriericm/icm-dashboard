"""Tests for pipeline.database — sequence reset and empty-input short-circuits."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pipeline.database import (
    Database,
    DatabaseConfigError,
    merge_genes_transactional,
    record_pipeline_run,
    record_processed_pmids_batch,
    reset_gene_sequence,
)

# ---------------------------------------------------------------------------
# Gene sequence reset
# ---------------------------------------------------------------------------


class TestResetGeneSequence:
    async def test_executes_fixed_statement(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        # Mock Database.connection() as an async context manager
        mocker.patch.object(
            Database,
            "connection",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            ),
        )

        await reset_gene_sequence()
        sql = mock_conn.execute.await_args.args[0]
        assert "genes_id_seq" in sql
        assert "MAX(id) FROM genes" in sql


# ---------------------------------------------------------------------------
# Empty input short-circuits
# ---------------------------------------------------------------------------


class TestEmptyInputShortCircuits:
    async def test_merge_empty_both(self):
        inserted, updated = await merge_genes_transactional([], [])
        assert inserted == 0
        assert updated == 0

    async def test_record_empty_pmids(self):
        count = await record_processed_pmids_batch([])
        assert count == 0


# ---------------------------------------------------------------------------
# Pipeline run recording
# ---------------------------------------------------------------------------


class TestRecordPipelineRun:
    async def test_inserts_and_returns_id(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=42)

        mocker.patch.object(
            Database,
            "connection",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            ),
        )

        row_id = await record_pipeline_run(
            run_timestamp="2026-03-24T10:00:00+00:00",
            papers_processed=5,
            fulltext_retrieved=3,
            genes_extracted=12,
            genes_validated=10,
            run_mode="standard",
        )
        assert row_id == 42
        mock_conn.fetchval.assert_awaited_once()
        sql = mock_conn.fetchval.call_args[0][0]
        assert "pipeline_runs" in sql

    async def test_rejects_missing_returned_id(self, mocker):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=None)

        mocker.patch.object(
            Database,
            "connection",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            ),
        )

        with pytest.raises(RuntimeError, match="integer pipeline run id"):
            await record_pipeline_run(
                run_timestamp="2026-03-24T10:00:00+00:00",
                papers_processed=5,
                fulltext_retrieved=3,
                genes_extracted=12,
                genes_validated=10,
            )


# ---------------------------------------------------------------------------
# DatabaseConfigError
# ---------------------------------------------------------------------------


class TestDatabaseConfigError:
    async def test_missing_env_vars_raises(self, monkeypatch):
        """Missing DB env vars should raise DatabaseConfigError."""
        # Clear all DB env vars
        for var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        Database._pool = None  # Force re-creation
        with pytest.raises(DatabaseConfigError, match="Missing required"):
            await Database.get_pool()

    async def test_partial_env_vars_raises(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "localhost")
        monkeypatch.delenv("DB_NAME", raising=False)
        monkeypatch.delenv("DB_USER", raising=False)
        monkeypatch.delenv("DB_PASSWORD", raising=False)

        Database._pool = None
        with pytest.raises(DatabaseConfigError, match="Missing required"):
            await Database.get_pool()

    async def test_error_message_lists_missing(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "localhost")
        monkeypatch.setenv("DB_NAME", "testdb")
        monkeypatch.delenv("DB_USER", raising=False)
        monkeypatch.delenv("DB_PASSWORD", raising=False)

        Database._pool = None
        with pytest.raises(DatabaseConfigError, match="DB_USER"):
            await Database.get_pool()


# ---------------------------------------------------------------------------
# Database singleton behavior
# ---------------------------------------------------------------------------


class TestDatabaseSingleton:
    def test_set_config(self):
        from pipeline.config import PipelineConfig

        cfg = PipelineConfig()
        Database.set_config(cfg)
        assert Database._config is cfg

    async def test_close_when_no_pool(self):
        """close() should not error when pool is None."""
        Database._pool = None
        await Database.close()
        assert Database._pool is None

    async def test_close_calls_pool_close(self):
        mock_pool = AsyncMock()
        Database._pool = mock_pool
        await Database.close()
        mock_pool.close.assert_awaited_once()
        assert Database._pool is None


# ---------------------------------------------------------------------------
# SQL correctness: PMID reference matching
# ---------------------------------------------------------------------------


class TestReferenceSqlPatterns:
    """Verify the reference merge SQL uses token-level union.

    These tests inspect the SQL strings in merge_genes_transactional to
    confirm that semicolon-delimited references are split and re-aggregated
    instead of compared as one string.
    """

    def test_update_does_not_use_like_for_reference_matching(self):
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        assert " LIKE " not in source

    def test_update_splits_and_reaggregates_references(self):
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        assert "string_to_array(COALESCE(\"references\", ''), ';')" in source
        assert "string_agg(val, '; ' ORDER BY first_ord)" in source
        assert "GROUP BY val" in source

    def test_update_preserves_and_unions_non_reference_evidence(self):
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        assert "mendelian_randomization = CASE" in source
        assert "genes.protein IS NULL" in source
        assert "COALESCE(gwas_trait, '')" in source

    def test_insert_has_on_conflict(self):
        """INSERT query must have ON CONFLICT for concurrent-run safety."""
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        assert "ON CONFLICT" in source
