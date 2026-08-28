"""Token-bucket rate limiter for async LLM API calls.

Proactively gates requests *before* they hit the API, preventing 429
errors rather than reacting to them. Supports both requests-per-minute
(RPM) and tokens-per-minute (TPM) tracking.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque

logger = logging.getLogger(__name__)

# Cap applied to every exponential backoff + retry-after delay.
BACKOFF_CAP_SECONDS: float = 64.0


def compute_backoff(base_delay: float, attempt: int) -> float:
    """Exponential backoff with +/-25% jitter, capped at the module cap.

    ``attempt`` is 1-based (attempt 1 returns ~``base_delay``). Jitter spreads
    concurrent retries so a fleet hitting the same 429 doesn't thunder back at
    identical moments.
    """
    raw = base_delay * (2 ** (attempt - 1))
    jittered = raw * random.uniform(0.75, 1.25)
    return min(jittered, BACKOFF_CAP_SECONDS)


def resolve_retry_delay(
    retry_after: str | None, backoff_delay: float
) -> tuple[float, str]:
    """Pick delay from a ``Retry-After`` header, falling back to ``backoff_delay``.

    Returns ``(delay, source)`` where ``source`` is a short label for logging
    ("retry-after=...s", "backoff (retry-after parse failed)", or "backoff").
    """
    if retry_after:
        try:
            return (
                min(float(retry_after), BACKOFF_CAP_SECONDS),
                f"retry-after={retry_after}s",
            )
        except ValueError:
            return backoff_delay, "backoff (retry-after parse failed)"
    return backoff_delay, "backoff"


class AsyncRateLimiter:
    """Token-bucket rate limiter for async LLM API calls.

    Tracks both requests-per-minute (RPM) and tokens-per-minute (TPM).
    A value of 0 for either limit disables that limit.

    Supports:
    - ``record_actual_usage()``: corrects pre-estimated token count with real usage
    - ``signal_rate_limit()``: triggers a global backoff so all pending ``acquire()``
      calls pause when any single call receives a 429
    """

    def __init__(self, rpm: int = 50, tpm: int = 100_000) -> None:
        self._rpm = rpm
        self._tpm = tpm
        self._request_times: deque[float] = deque()
        self._token_log: deque[tuple[float, int, int]] = deque()
        self._token_total: int = 0  # Running sum for O(1) TPM checks
        self._lock = asyncio.Lock()
        self._global_backoff_until: float = 0.0
        self._next_request_id: int = 0

    async def acquire(self, estimated_tokens: int = 2000) -> int:
        """Wait until both RPM and TPM budgets allow a new request.

        Returns:
            A request ID that must be passed to ``record_actual_usage()``
            to correct the token estimate for this specific request.
        """
        while True:
            async with self._lock:
                now = asyncio.get_running_loop().time()

                # Respect global backoff from 429 signals.
                if now < self._global_backoff_until:
                    remaining = self._global_backoff_until - now
                    sleep_time = min(
                        remaining + random.uniform(0, 0.3),
                        BACKOFF_CAP_SECONDS,
                    )
                    # Release lock and sleep below
                else:
                    cutoff = now - 60.0

                    # Prune entries older than 60s
                    while self._request_times and self._request_times[0] <= cutoff:
                        self._request_times.popleft()
                    while self._token_log and self._token_log[0][0] <= cutoff:
                        _, pruned_tokens, _ = self._token_log.popleft()
                        self._token_total -= pruned_tokens

                    rpm_ok = self._rpm == 0 or len(self._request_times) < self._rpm
                    tpm_ok = (
                        self._tpm == 0
                        or (self._token_total + estimated_tokens) <= self._tpm
                    )

                    if rpm_ok and tpm_ok:
                        request_id = self._next_request_id
                        self._next_request_id += 1
                        self._request_times.append(now)
                        self._token_log.append((now, estimated_tokens, request_id))
                        self._token_total += estimated_tokens
                        return request_id

                    # Compute precise sleep: time until the oldest request expires
                    if self._request_times and self._rpm > 0:
                        sleep_time = max(0.05, self._request_times[0] + 60.0 - now)
                        sleep_time = min(sleep_time, 1.0)
                    else:
                        sleep_time = 0.1

            await asyncio.sleep(sleep_time)

    async def record_actual_usage(self, request_id: int, actual_tokens: int) -> None:
        """Correct the pre-estimated token count with actual usage.

        Finds the token log entry matching *request_id* and replaces
        its token count with *actual_tokens*. Pass ``actual_tokens=0``
        to release a reservation after a failed API call.
        """
        async with self._lock:
            for i in range(len(self._token_log) - 1, -1, -1):
                ts, tokens, rid = self._token_log[i]
                if rid == request_id:
                    self._token_log[i] = (ts, actual_tokens, rid)
                    self._token_total += actual_tokens - tokens
                    return
            logger.debug(
                "record_actual_usage: request_id %d not found "
                "(likely pruned after 60s TTL)",
                request_id,
            )

    async def signal_rate_limit(self, backoff_seconds: float) -> None:
        """Signal that a 429 was received — all pending acquire() calls will pause.

        Sets a global backoff deadline so concurrent tasks stop sending requests
        until the backoff expires, preventing a thundering herd. Acquires the
        shared lock so the deadline update is serialized with acquire()'s reads.
        """
        async with self._lock:
            now = asyncio.get_running_loop().time()
            new_deadline = now + backoff_seconds
            # Only extend, never shorten an existing backoff
            self._global_backoff_until = max(self._global_backoff_until, new_deadline)
