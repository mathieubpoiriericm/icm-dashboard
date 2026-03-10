"""Tests for pipeline.database — whitelist validation, empty-input short-circuits."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pipeline.config import ALLOWED_COLUMNS, ALLOWED_TABLES
from pipeline.database import (
    Database,
    DatabaseConfigError,
    merge_genes_transactional,
    record_processed_pmids_batch,
    reset_sequence,
)

# ---------------------------------------------------------------------------
# Whitelist validation for reset_sequence
# ---------------------------------------------------------------------------


class TestResetSequenceWhitelist:
    async def test_invalid_table_raises(self):
        with pytest.raises(ValueError, match="not in allowed list"):
            await reset_sequence("evil_table")

    async def test_invalid_column_raises(self):
        with pytest.raises(ValueError, match="not in allowed list"):
            await reset_sequence("genes", column="evil_column")

    async def test_valid_table_valid_column(self, mocker):
        """Valid table + column should proceed to DB call (which we mock)."""
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=["genes", "id", "'genes_id_seq'"])
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

        await reset_sequence("genes", "id")
        mock_conn.execute.assert_awaited_once()


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
# Allowed tables/columns consistency
# ---------------------------------------------------------------------------


class TestAllowedLists:
    def test_genes_table_allowed(self):
        assert "genes" in ALLOWED_TABLES

    def test_pubmed_refs_allowed(self):
        assert "pubmed_refs" in ALLOWED_TABLES

    def test_id_column_allowed(self):
        assert "id" in ALLOWED_COLUMNS

    def test_all_tables_are_strings(self):
        for t in ALLOWED_TABLES:
            assert isinstance(t, str)

    def test_all_columns_are_strings(self):
        for c in ALLOWED_COLUMNS:
            assert isinstance(c, str)


# ---------------------------------------------------------------------------
# SQL correctness: PMID reference matching
# ---------------------------------------------------------------------------


class TestReferenceSqlPatterns:
    """Verify the reference-matching SQL logic prevents substring false positives.

    These tests inspect the SQL strings in merge_genes_transactional to
    confirm that exact token matching is used, not substring LIKE.
    """

    def test_update_uses_exact_token_matching(self):
        """UPDATE query must NOT use substring LIKE for reference matching."""
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        # Old buggy pattern: LIKE '%' || $3 || '%'
        assert "'%' || $3 || '%'" not in source, (
            "UPDATE still uses substring LIKE — PMID '1234' would match '12345'"
        )

    def test_update_has_exact_match_clauses(self):
        """UPDATE query should use exact semicolon-delimited token matching."""
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        # Exact match: "references" = $3
        assert '"references" = $3' in source
        # Starts with: "references" LIKE $3 || '; %'
        assert "$3 || '; %'" in source
        # Ends with: "references" LIKE '%; ' || $3
        assert "'%; ' || $3" in source

    def test_insert_has_on_conflict(self):
        """INSERT query must have ON CONFLICT for concurrent-run safety."""
        import inspect

        source = inspect.getsource(merge_genes_transactional)
        assert "ON CONFLICT" in source
