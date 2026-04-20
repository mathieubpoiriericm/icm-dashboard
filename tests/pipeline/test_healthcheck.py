import logging
from unittest.mock import AsyncMock

import pytest

from pipeline.healthcheck import (
    close_healthcheck_client,
    ping_failure,
    ping_start,
    ping_success,
)


@pytest.fixture(autouse=True)
async def _reset_client():
    """Ensure each test starts and ends with no shared client instance."""
    await close_healthcheck_client()
    yield
    await close_healthcheck_client()


async def test_empty_url_noop():
    """No exception when URL is empty."""
    await ping_start("")
    await ping_success("")
    await ping_failure("", "some error")


async def test_ping_start(mocker):
    """ping_start sends GET to {url}/start."""
    mock_client = AsyncMock()
    mocker.patch("pipeline.healthcheck._client_manager.get", return_value=mock_client)
    await ping_start("http://hc.local/ping/abc")
    mock_client.get.assert_awaited_once()
    assert "/start" in str(mock_client.get.call_args)


async def test_ping_success(mocker):
    """ping_success sends GET to the base URL."""
    mock_client = AsyncMock()
    mocker.patch("pipeline.healthcheck._client_manager.get", return_value=mock_client)
    await ping_success("http://hc.local/ping/abc")
    mock_client.get.assert_awaited_once()
    assert mock_client.get.call_args[0][0] == "http://hc.local/ping/abc"


async def test_ping_failure(mocker):
    """ping_failure sends POST to {url}/fail with message body."""
    mock_client = AsyncMock()
    mocker.patch("pipeline.healthcheck._client_manager.get", return_value=mock_client)
    await ping_failure("http://hc.local/ping/abc", "traceback here")
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert "/fail" in call_args[0][0]
    assert call_args[1]["content"] == "traceback here"


async def test_ping_start_graceful_on_error(mocker, caplog):
    """Network error is logged as warning, not raised."""
    mock_client = AsyncMock()
    mock_client.get.side_effect = ConnectionError("unreachable")
    mocker.patch("pipeline.healthcheck._client_manager.get", return_value=mock_client)
    with caplog.at_level(logging.WARNING):
        await ping_start("http://hc.local/ping/abc")
    assert "Healthcheck start ping failed" in caplog.text


async def test_ping_success_graceful_on_error(mocker, caplog):
    """Network error on success ping is logged, not raised."""
    mock_client = AsyncMock()
    mock_client.get.side_effect = ConnectionError("unreachable")
    mocker.patch("pipeline.healthcheck._client_manager.get", return_value=mock_client)
    with caplog.at_level(logging.WARNING):
        await ping_success("http://hc.local/ping/abc")
    assert "Healthcheck success ping failed" in caplog.text


async def test_ping_failure_graceful_on_error(mocker, caplog):
    """Network error on failure ping is logged, not raised."""
    mock_client = AsyncMock()
    mock_client.post.side_effect = ConnectionError("unreachable")
    mocker.patch("pipeline.healthcheck._client_manager.get", return_value=mock_client)
    with caplog.at_level(logging.WARNING):
        await ping_failure("http://hc.local/ping/abc", "error")
    assert "Healthcheck failure ping failed" in caplog.text


async def test_close_healthcheck_client_idempotent():
    """close_healthcheck_client is safe to call when no client has been created."""
    await close_healthcheck_client()
    await close_healthcheck_client()
