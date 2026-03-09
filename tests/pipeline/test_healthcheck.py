import logging
from unittest.mock import patch

from pipeline.healthcheck import ping_failure, ping_start, ping_success


def test_empty_url_noop():
    """No exception when URL is empty."""
    ping_start("")
    ping_success("")
    ping_failure("", "some error")


@patch("pipeline.healthcheck.httpx.get")
def test_ping_start(mock_get):
    """ping_start sends GET to {url}/start."""
    ping_start("http://hc.local/ping/abc")
    mock_get.assert_called_once()
    assert "/start" in str(mock_get.call_args)


@patch("pipeline.healthcheck.httpx.get")
def test_ping_success(mock_get):
    """ping_success sends GET to the base URL."""
    ping_success("http://hc.local/ping/abc")
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert call_args[0][0] == "http://hc.local/ping/abc"


@patch("pipeline.healthcheck.httpx.post")
def test_ping_failure(mock_post):
    """ping_failure sends POST to {url}/fail with message body."""
    ping_failure("http://hc.local/ping/abc", "traceback here")
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "/fail" in call_args[0][0]
    assert call_args[1]["content"] == "traceback here"


@patch("pipeline.healthcheck.httpx.get")
def test_ping_start_graceful_on_error(mock_get, caplog):
    """Network error is logged as warning, not raised."""
    mock_get.side_effect = ConnectionError("unreachable")

    with caplog.at_level(logging.WARNING):
        ping_start("http://hc.local/ping/abc")

    assert "Healthcheck start ping failed" in caplog.text


@patch("pipeline.healthcheck.httpx.get")
def test_ping_success_graceful_on_error(mock_get, caplog):
    """Network error on success ping is logged, not raised."""
    mock_get.side_effect = ConnectionError("unreachable")

    with caplog.at_level(logging.WARNING):
        ping_success("http://hc.local/ping/abc")

    assert "Healthcheck success ping failed" in caplog.text


@patch("pipeline.healthcheck.httpx.post")
def test_ping_failure_graceful_on_error(mock_post, caplog):
    """Network error on failure ping is logged, not raised."""
    mock_post.side_effect = ConnectionError("unreachable")

    with caplog.at_level(logging.WARNING):
        ping_failure("http://hc.local/ping/abc", "error")

    assert "Healthcheck failure ping failed" in caplog.text
