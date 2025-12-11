"""Shared LRU cache eviction utilities for pipeline modules."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE: Final[int] = 10_000
DEFAULT_EVICT_FRACTION: Final[float] = 0.2


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
    """
    if len(cache) < max_size:
        return 0
    evict_count = int(max_size * evict_fraction)
    for _ in range(evict_count):
        cache.popitem(last=False)
    logger.debug(f"Evicted {evict_count} oldest entries from {label}")
    return evict_count
