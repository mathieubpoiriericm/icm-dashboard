"""Probe vLLM's /v1/models endpoint until it returns 200, or time out."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)


async def wait_until_ready(
    base_url: str,
    timeout: float = 900.0,
    poll_interval: float = 5.0,
) -> None:
    """Poll <base_url>/v1/models until it returns 200, or raise TimeoutError.

    The default 900s ceiling reflects the cold-load time of Gemma 4 31B at
    4-bit over NFSv3 (~17 GB read into V100 VRAM, typically 2-6 minutes).
    """
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    base_url = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{base_url}/v1/models")
                if r.status_code == 200:
                    return
                last_error = RuntimeError(f"got status {r.status_code}")
            except httpx.HTTPError as e:
                last_error = e
            await asyncio.sleep(poll_interval)
    raise TimeoutError(f"vLLM at {base_url} not ready after {timeout}s: {last_error!r}")
