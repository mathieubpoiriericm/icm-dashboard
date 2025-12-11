"""Token-bucket rate limiter for async LLM API calls.

Proactively gates requests *before* they hit the API, preventing 429
errors rather than reacting to them. Supports both requests-per-minute
(RPM) and tokens-per-minute (TPM) tracking.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque


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
        self._token_log: deque[tuple[float, int]] = deque()
        self._token_total: int = 0  # Running sum for O(1) TPM checks
        self._lock = asyncio.Lock()
        self._global_backoff_until: float = 0.0

    async def acquire(self, estimated_tokens: int = 2000) -> None:
        """Wait until both RPM and TPM budgets allow a new request."""
        while True:
            async with self._lock:
                now = asyncio.get_running_loop().time()

                # Respect global backoff from 429 signals
                if now < self._global_backoff_until:
                    remaining = self._global_backoff_until - now
                    sleep_time = min(remaining + random.uniform(0, 0.3), 2.0)
                    # Release lock and sleep below
                else:
                    cutoff = now - 60.0

                    # Prune entries older than 60s
                    while self._request_times and self._request_times[0] <= cutoff:
                        self._request_times.popleft()
                    while self._token_log and self._token_log[0][0] <= cutoff:
                        _, pruned_tokens = self._token_log.popleft()
                        self._token_total -= pruned_tokens

                    rpm_ok = self._rpm == 0 or len(self._request_times) < self._rpm
                    tpm_ok = (
                        self._tpm == 0
                        or (self._token_total + estimated_tokens) <= self._tpm
                    )

                    if rpm_ok and tpm_ok:
                        self._request_times.append(now)
                        self._token_log.append((now, estimated_tokens))
                        self._token_total += estimated_tokens
                        return

                    # Compute precise sleep: time until the oldest request expires
                    if self._request_times and self._rpm > 0:
                        sleep_time = max(0.05, self._request_times[0] + 60.0 - now)
                        sleep_time = min(sleep_time, 1.0)
                    else:
                        sleep_time = 0.1

            await asyncio.sleep(sleep_time)

    async def record_actual_usage(
        self, estimated_tokens: int, actual_tokens: int
    ) -> None:
        """Correct the pre-estimated token count with actual usage.

        Finds the most recent token log entry matching *estimated_tokens* and replaces
        it with *actual_tokens*. This improves TPM tracking accuracy for subsequent
        calls in the same 60s window.
        """
        async with self._lock:
            # Walk backwards to find and correct the matching estimate
            for i in range(len(self._token_log) - 1, -1, -1):
                ts, tokens = self._token_log[i]
                if tokens == estimated_tokens:
                    self._token_log[i] = (ts, actual_tokens)
                    self._token_total += actual_tokens - estimated_tokens
                    return

    def signal_rate_limit(self, backoff_seconds: float) -> None:
        """Signal that a 429 was received — all pending acquire() calls will pause.

        Sets a global backoff deadline so concurrent tasks stop sending requests
        until the backoff expires, preventing a thundering herd.
        """
        now = asyncio.get_running_loop().time()
        new_deadline = now + backoff_seconds
        # Only extend, never shorten an existing backoff
        if new_deadline > self._global_backoff_until:
            self._global_backoff_until = new_deadline
