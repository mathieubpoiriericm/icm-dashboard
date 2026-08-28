"""Tests for shared cache and batch-fetch helpers."""

from __future__ import annotations

import asyncio

from pipeline.cache_utils import run_batched_fetch


async def test_run_batched_fetch_preserves_order_and_reports_each_completion():
    progress: list[tuple[int, int]] = []

    async def fetch_one(value: int) -> int:
        await asyncio.sleep(0)
        return value * 2

    results = await run_batched_fetch(
        [1, 2, 3, 4, 5],
        fetch_one,
        progress_callback=lambda current, total: progress.append((current, total)),
        chunk_size=2,
    )

    assert results == [2, 4, 6, 8, 10]
    assert progress == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
