"""Shared LRU cache eviction utilities for pipeline modules."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE: Final[int] = 10_000
DEFAULT_EVICT_FRACTION: Final[float] = 0.2

# Max tasks alive in the event loop at once for batched external fetches.
# Bounds queue memory without materially hurting throughput (the per-module
# semaphore still bounds concurrent HTTP calls).
BATCH_CHUNK_SIZE: Final[int] = 50

# Treat rows in on-disk API caches older than this as stale and re-fetch.
DB_CACHE_TTL_DAYS: Final[int] = 30


@dataclass(slots=True)
class SyncResult:
    """Result of sync operation."""

    fetched: int
    cached: int
    failed: int
    errors: list[str]


def make_log_progress(label: str, interval: int = 10):
    """Create a progress callback that logs every *interval* items.

    Args:
        label: Prefix for the log message (e.g. "NCBI fetch").
        interval: Log every N items (also logs the final item).

    Returns:
        A callback(current, total) suitable for batch fetch functions.
    """

    def _log_progress(current: int, total: int) -> None:
        if current % interval == 0 or current == total:
            logger.info(f"  {label} progress: {current}/{total}")

    return _log_progress


async def run_batched_fetch[T, R](
    items: Sequence[T],
    fetch_one: Callable[[T], Awaitable[R]],
    progress_callback: Callable[[int, int], None] | None = None,
    chunk_size: int = BATCH_CHUNK_SIZE,
) -> list[R]:
    """Run ``fetch_one`` on every item, in bounded-size concurrent chunks.

    Each chunk runs inside an ``asyncio.TaskGroup`` so all tasks in a chunk
    complete (or one raises) before the next chunk starts — this bounds
    peak task-queue memory without relying on the caller's semaphore.
    """
    total = len(items)
    completed = 0
    completed_lock = asyncio.Lock()

    async def _tracked(item: T) -> R:
        nonlocal completed
        result = await fetch_one(item)
        async with completed_lock:
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total)
        return result

    results: list[R] = []
    for start in range(0, total, chunk_size):
        chunk = items[start : start + chunk_size]
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_tracked(x)) for x in chunk]
        results.extend(t.result() for t in tasks)
    return results


def evict_lru(
    cache: OrderedDict[str, Any],
    max_size: int = DEFAULT_MAX_SIZE,
    evict_fraction: float = DEFAULT_EVICT_FRACTION,
    label: str = "cache",
) -> int:
    """Evict oldest entries when cache exceeds max_size.

    Args:
        cache: OrderedDict used as an LRU cache.
        max_size: Maximum number of entries before eviction triggers.
        evict_fraction: Fraction of max_size to evict (0.0–1.0).
        label: Human-readable name for log messages.

    Returns:
        Number of entries evicted.

    Concurrency contract: callers must hold the cache's async lock across both
    the insert and the evict call (i.e. no ``await`` between them). OrderedDict
    is not safe for concurrent mutation across awaits.
    """
    if len(cache) < max_size:
        return 0
    evict_count = int(max_size * evict_fraction)
    for _ in range(evict_count):
        cache.popitem(last=False)
    logger.debug(f"Evicted {evict_count} oldest entries from {label}")
    return evict_count


async def single_flight_get[T](
    key: str,
    *,
    cache: OrderedDict[str, T | None],
    cache_lock: asyncio.Lock,
    in_flight: dict[str, asyncio.Task[T | None]],
    semaphore: asyncio.Semaphore,
    fetch_fn: Callable[[], Awaitable[T | None]],
    label: str,
) -> T | None:
    """Cache-backed fetch that deduplicates concurrent callers for the same key.

    Warm-cache reads bypass the lock. On a miss, exactly one upstream request
    fires per key regardless of how many tasks race in — peers share the
    same in-flight ``asyncio.Task`` via ``asyncio.shield`` so one caller's
    cancellation cannot cancel the fetch for others.

    Callers are responsible for key normalisation (e.g. ``.upper()``) and for
    initialising ``cache_lock`` / ``semaphore`` inside a running event loop
    before the first call.
    """

    async def _fetch_and_store() -> T | None:
        try:
            async with semaphore:
                result = await fetch_fn()
            async with cache_lock:
                evict_lru(cache, DEFAULT_MAX_SIZE, DEFAULT_EVICT_FRACTION, label)
                cache[key] = result
            return result
        finally:
            in_flight.pop(key, None)

    if key in cache:
        return cache[key]

    async with cache_lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        task = in_flight.get(key)
        if task is None:
            task = asyncio.create_task(_fetch_and_store())
            in_flight[key] = task

    return await asyncio.shield(task)
