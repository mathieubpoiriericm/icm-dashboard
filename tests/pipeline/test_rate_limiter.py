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
        request_id = await rl.acquire(estimated_tokens=1000)
        assert isinstance(request_id, int)
        assert len(rl._request_times) == 1
        assert len(rl._token_log) == 1

    async def test_multiple_acquires(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        ids = []
        for _ in range(5):
            ids.append(await rl.acquire(estimated_tokens=1000))
        assert len(rl._request_times) == 5
        assert rl._token_total == 5000
        # Each request gets a unique ID
        assert len(set(ids)) == 5

    async def test_disabled_rpm_always_allows(self):
        rl = AsyncRateLimiter(rpm=0, tpm=0)
        for _ in range(100):
            await rl.acquire(estimated_tokens=1000)
        assert len(rl._request_times) == 100

    async def test_tpm_tracking(self):
        rl = AsyncRateLimiter(rpm=100, tpm=100_000)
        await rl.acquire(estimated_tokens=50_000)
        assert rl._token_total == 50_000

    async def test_request_ids_are_monotonic(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        id1 = await rl.acquire(estimated_tokens=1000)
        id2 = await rl.acquire(estimated_tokens=1000)
        id3 = await rl.acquire(estimated_tokens=1000)
        assert id1 < id2 < id3


class TestRecordActualUsage:
    async def test_corrects_estimate_by_request_id(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        request_id = await rl.acquire(estimated_tokens=5000)
        assert rl._token_total == 5000

        await rl.record_actual_usage(request_id, 3000)
        assert rl._token_total == 3000

    async def test_no_match_does_nothing(self):
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        await rl.acquire(estimated_tokens=5000)
        await rl.record_actual_usage(9999, 1000)
        # No match found, total unchanged
        assert rl._token_total == 5000

    async def test_corrects_exact_request(self):
        """With request IDs, the correct entry is always updated."""
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        id1 = await rl.acquire(estimated_tokens=5000)
        id2 = await rl.acquire(estimated_tokens=5000)
        assert rl._token_total == 10_000

        # Correct the FIRST request, not the most recent
        await rl.record_actual_usage(id1, 2000)
        assert rl._token_total == 7000
        # Verify the correct entry was updated
        assert rl._token_log[0][1] == 2000  # id1's entry
        assert rl._token_log[1][1] == 5000  # id2's entry unchanged

    async def test_concurrent_same_estimate_corrects_right_entry(self):
        """Bug 1 regression: concurrent requests with identical estimates."""
        rl = AsyncRateLimiter(rpm=10, tpm=1_000_000)
        # Simulate 5 concurrent papers, all estimating 40K tokens
        ids = []
        for _ in range(5):
            ids.append(await rl.acquire(estimated_tokens=40_000))
        assert rl._token_total == 200_000

        # Paper A (ids[0]) finishes first with actual=35K
        await rl.record_actual_usage(ids[0], 35_000)
        assert rl._token_log[0][1] == 35_000
        # Other entries unchanged
        for i in range(1, 5):
            assert rl._token_log[i][1] == 40_000
        assert rl._token_total == 195_000

        # Paper C (ids[2]) finishes with actual=45K
        await rl.record_actual_usage(ids[2], 45_000)
        assert rl._token_log[2][1] == 45_000
        assert rl._token_total == 200_000

    async def test_zero_tokens_releases_reservation(self):
        """Bug 2: failed API call should release reservation."""
        rl = AsyncRateLimiter(rpm=10, tpm=100_000)
        request_id = await rl.acquire(estimated_tokens=40_000)
        assert rl._token_total == 40_000

        # API call failed — zero out the reservation
        await rl.record_actual_usage(request_id, 0)
        assert rl._token_total == 0


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
