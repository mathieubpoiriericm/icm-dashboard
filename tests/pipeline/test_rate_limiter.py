"""Tests for pipeline.rate_limiter — RPM/TPM limiting and backoff."""

from __future__ import annotations

from pipeline.rate_limiter import AsyncRateLimiter


class TestRateLimiterInit:
    def test_default_limits(self):
        rl = AsyncRateLimiter()
        assert rl._rpm == 50
        assert rl._tpm == 100_000

    def test_custom_limits(self):
        rl = AsyncRateLimiter(rpm=10, tpm=50_000)
        assert rl._rpm == 10
        assert rl._tpm == 50_000

    def test_zero_rpm_disables(self):
        rl = AsyncRateLimiter(rpm=0)
        assert rl._rpm == 0

    def test_zero_tpm_disables(self):
        rl = AsyncRateLimiter(tpm=0)
        assert rl._tpm == 0


class TestAcquire:
    async def test_single_acquire(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        await rl.acquire(estimated_tokens=1000)
        assert len(rl._request_times) == 1
        assert len(rl._token_log) == 1

    async def test_multiple_acquires(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        for _ in range(5):
            await rl.acquire(estimated_tokens=1000)
        assert len(rl._request_times) == 5
        assert rl._token_total == 5000

    async def test_disabled_rpm_always_allows(self):
        rl = AsyncRateLimiter(rpm=0, tpm=0)
        for _ in range(100):
            await rl.acquire(estimated_tokens=1000)
        assert len(rl._request_times) == 100

    async def test_tpm_tracking(self):
        rl = AsyncRateLimiter(rpm=100, tpm=100_000)
        await rl.acquire(estimated_tokens=50_000)
        assert rl._token_total == 50_000


class TestRecordActualUsage:
    async def test_corrects_estimate(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        await rl.acquire(estimated_tokens=5000)
        assert rl._token_total == 5000

        await rl.record_actual_usage(5000, 3000)
        assert rl._token_total == 3000

    async def test_no_match_does_nothing(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        await rl.acquire(estimated_tokens=5000)
        await rl.record_actual_usage(9999, 1000)
        # No match found, total unchanged
        assert rl._token_total == 5000

    async def test_corrects_most_recent_match(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        await rl.acquire(estimated_tokens=5000)
        await rl.acquire(estimated_tokens=5000)
        assert rl._token_total == 10_000

        await rl.record_actual_usage(5000, 2000)
        # Should correct the LAST 5000 entry
        assert rl._token_total == 7000


class TestSignalRateLimit:
    async def test_sets_backoff(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        rl.signal_rate_limit(5.0)
        assert rl._global_backoff_until > 0

    async def test_extends_backoff(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        rl.signal_rate_limit(5.0)
        first = rl._global_backoff_until
        # Extend with longer backoff
        rl.signal_rate_limit(10.0)
        assert rl._global_backoff_until > first

    async def test_does_not_shorten_backoff(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        rl.signal_rate_limit(10.0)
        long_deadline = rl._global_backoff_until
        # Try to shorten — should be ignored
        rl.signal_rate_limit(1.0)
        assert rl._global_backoff_until == long_deadline
