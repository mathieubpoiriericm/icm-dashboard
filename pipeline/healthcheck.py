"""Healthchecks.io dead-man's-switch integration.

All functions are non-fatal: network errors are logged as warnings
but never propagate. No-op when the URL is empty.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


def ping_start(url: str) -> None:
    """Signal that the pipeline run has started.

    Args:
        url: Healthchecks.io ping URL (e.g. ``http://hc.local/ping/<uuid>``).
    """
    if not url:
        return
    try:
        httpx.get(f"{url}/start", timeout=_TIMEOUT)
        logger.debug("Healthcheck start ping sent")
    except Exception as exc:
        logger.warning(f"Healthcheck start ping failed: {exc}")


def ping_success(url: str) -> None:
    """Signal that the pipeline run completed successfully.

    Args:
        url: Healthchecks.io ping URL.
    """
    if not url:
        return
    try:
        httpx.get(url, timeout=_TIMEOUT)
        logger.debug("Healthcheck success ping sent")
    except Exception as exc:
        logger.warning(f"Healthcheck success ping failed: {exc}")


def ping_failure(url: str, message: str) -> None:
    """Signal that the pipeline run failed.

    Args:
        url: Healthchecks.io ping URL.
        message: Error details (sent as POST body).
    """
    if not url:
        return
    try:
        httpx.post(f"{url}/fail", content=message, timeout=_TIMEOUT)
        logger.debug("Healthcheck failure ping sent")
    except Exception as exc:
        logger.warning(f"Healthcheck failure ping failed: {exc}")
