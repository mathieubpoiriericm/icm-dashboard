"""Tests for pipeline_app_hpc.hpc.readiness."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


class TestWaitUntilReady:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_200(self, mocker):
        from pipeline_app_hpc.hpc.readiness import wait_until_ready

        resp = MagicMock(status_code=200)
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=resp)
        client_mock.aclose = AsyncMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline_app_hpc.hpc.readiness.httpx.AsyncClient",
            return_value=client_mock,
        )
        sleep_mock = AsyncMock()
        mocker.patch("pipeline_app_hpc.hpc.readiness.asyncio.sleep", new=sleep_mock)
        await wait_until_ready("http://127.0.0.1:30800", timeout=10, poll_interval=1)
        client_mock.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_polls_then_succeeds(self, mocker):
        from pipeline_app_hpc.hpc.readiness import wait_until_ready

        first = MagicMock(status_code=503)
        second = MagicMock(status_code=200)
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(side_effect=[first, second])
        client_mock.aclose = AsyncMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline_app_hpc.hpc.readiness.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch("pipeline_app_hpc.hpc.readiness.asyncio.sleep", new=AsyncMock())
        await wait_until_ready("http://127.0.0.1:30800", timeout=10, poll_interval=0.1)
        assert client_mock.get.await_count == 2

    @pytest.mark.asyncio
    async def test_tolerates_connect_error(self, mocker):
        from pipeline_app_hpc.hpc.readiness import wait_until_ready

        ok = MagicMock(status_code=200)
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("not yet"),
                ok,
            ]
        )
        client_mock.aclose = AsyncMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline_app_hpc.hpc.readiness.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch("pipeline_app_hpc.hpc.readiness.asyncio.sleep", new=AsyncMock())
        await wait_until_ready("http://127.0.0.1:30800", timeout=10, poll_interval=0.1)
        assert client_mock.get.await_count == 2

    @pytest.mark.asyncio
    async def test_tolerates_http_timeout(self, mocker):
        from pipeline_app_hpc.hpc.readiness import wait_until_ready

        ok = MagicMock(status_code=200)
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("not yet"),
                ok,
            ]
        )
        client_mock.aclose = AsyncMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline_app_hpc.hpc.readiness.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch("pipeline_app_hpc.hpc.readiness.asyncio.sleep", new=AsyncMock())
        await wait_until_ready("http://127.0.0.1:30800", timeout=10, poll_interval=0.1)
        assert client_mock.get.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self, mocker):
        from pipeline_app_hpc.hpc.readiness import wait_until_ready

        bad = MagicMock(status_code=503)
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.get = AsyncMock(return_value=bad)
        client_mock.aclose = AsyncMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        mocker.patch(
            "pipeline_app_hpc.hpc.readiness.httpx.AsyncClient",
            return_value=client_mock,
        )
        mocker.patch("pipeline_app_hpc.hpc.readiness.asyncio.sleep", new=AsyncMock())
        # monkeypatch monotonic so the first call returns 0 and the next >= timeout
        monos = iter([0.0, 0.0, 100.0])
        mocker.patch(
            "pipeline_app_hpc.hpc.readiness.time.monotonic",
            lambda: next(monos, 100.0),
        )
        with pytest.raises(TimeoutError, match="not ready"):
            await wait_until_ready(
                "http://127.0.0.1:30800", timeout=10, poll_interval=0.1
            )
