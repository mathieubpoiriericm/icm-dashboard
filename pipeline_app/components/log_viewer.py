"""Log viewer with line cap and per-line severity coloring."""

from __future__ import annotations

import re
from collections import deque
from contextlib import suppress
from typing import Literal, get_args

from nicegui import context, ui

MAX_LOG_LINES: int = 10_000

Severity = Literal["info", "warn", "error", "stderr", "debug"]

_VALID_SEVERITIES: frozenset[str] = frozenset(get_args(Severity))

# Ordered: first match wins. Bracketed tokens are checked too because many
# loggers emit lines like "[ERROR] something failed". The Traceback pattern
# matches the literal Python stack-trace header so casual log lines that
# merely mention the word "traceback" don't get miscoloured as errors.
_SEVERITY_PATTERNS: tuple[tuple[re.Pattern[str], Severity], ...] = (
    (
        re.compile(
            r"\[?\bERROR\b\]?|\bCRITICAL\b|\bFATAL\b"
            r"|Traceback \(most recent call last\):",
            re.IGNORECASE,
        ),
        "error",
    ),
    (
        re.compile(r"^([\w.]+\.)?[A-Z]\w*(Error|Exception|Warning):"),
        "error",
    ),
    (re.compile(r"\[?\bWARN(ING)?\b\]?", re.IGNORECASE), "warn"),
    (re.compile(r"\[?\bDEBUG\b\]?", re.IGNORECASE), "debug"),
)


def detect_severity(line: str) -> Severity:
    """Return the inferred severity for a log line (default ``info``).

    Used when callers don't pass an explicit severity. Errors win over
    warnings win over debug so a traceback with WARN in it still lights
    up as an error.
    """
    for pattern, severity in _SEVERITY_PATTERNS:
        if pattern.search(line):
            return severity
    return "info"


#: CSS class for stderr log lines — exposed so remount-replay paths can
#: construct (line, class) tuples for ``LogViewer.load_batch`` without
#: hard-coding the naming convention.
STDERR_CSS_CLASS: str = "log-line-stderr"


def css_class_for(severity: Severity) -> str:
    """CSS class for a stdout log line of a given severity."""
    return f"log-line-{severity}"


#: Flush cadence for batched log writes. 50 ms keeps interactive latency
#: imperceptible while collapsing a 1000-line/sec burst into ~20 update
#: cycles per second instead of ~1000.
_FLUSH_INTERVAL_S: float = 0.05

#: Hard cap on the in-memory pending batch. If the producer outruns the UI
#: beyond this, drop oldest pending lines — NiceGUI's underlying ui.log
#: already caps *rendered* lines at MAX_LOG_LINES, so losing unseen tail is
#: preferable to unbounded memory growth during a pathological run.
_PENDING_CAP: int = 5000


class LogViewer:
    """Wraps ui.log with a line cap and batched, severity-coloured appends."""

    def __init__(self) -> None:
        self._log = ui.log(max_lines=MAX_LOG_LINES).classes("w-full h-96 theme-log")
        # Bounded deque gives O(1) drop-oldest on overflow — avoids the O(n)
        # slice-and-delete a plain list would need to honour the cap.
        self._buf: deque[tuple[str, str]] = deque(maxlen=_PENDING_CAP)
        # Keep a handle on the timer so stop() can cancel it — ui.timer is
        # NOT auto-cancelled on container teardown (NiceGUI issues #1500,
        # #4617), so a fresh LogViewer on each page render would otherwise
        # leak one flush-tick-per-50-ms coroutine per navigation.
        self._timer = ui.timer(_FLUSH_INTERVAL_S, self._flush)
        # Register cleanup on client disconnect so per-navigation timers
        # don't accumulate across a long-lived browser session.
        with suppress(Exception):
            context.client.on_disconnect(self.stop)

    def append(self, line: str, severity: Severity | None = None) -> None:
        """Queue a log line with optional explicit severity.

        Args:
            line: A single log line. Callers must strip newlines —
                embedded ``\\n`` is rendered verbatim inside one
                ``ui.log`` entry.
            severity: Explicit severity. If None, inferred via
                ``detect_severity``.
        """
        sev = severity if severity in _VALID_SEVERITIES else detect_severity(line)
        self._buf.append((line, css_class_for(sev)))

    def append_stderr(self, line: str) -> None:
        """Queue a stderr line (prefixed with ``[stderr]``)."""
        self._buf.append((f"[stderr] {line}", STDERR_CSS_CLASS))

    def load_batch(self, entries: list[tuple[str, str]]) -> None:
        """Bulk replay path for remount: extend + immediate flush.

        Bypasses the timer so the UI paints the replay in a single cycle
        instead of waiting out N × 50 ms ticks when thousands of lines are
        restored after a page re-mount.
        """
        if not entries:
            return
        self._buf.extend(entries)
        self._flush()

    def clear(self) -> None:
        """Clear rendered content and any pending batch."""
        self._buf.clear()
        self._log.clear()

    def stop(self) -> None:
        """Cancel the flush timer. Call before discarding a LogViewer.

        Without this, a LogViewer created on each page render leaks a live
        ui.timer per navigation — the callback fires every 50 ms on a
        disposed element for the rest of the client session.
        """
        with suppress(Exception):
            self._timer.active = False
        with suppress(Exception):
            self._timer.delete()

    def _flush(self) -> None:
        if not self._buf:
            return
        # Pop one at a time so a disconnect mid-batch doesn't silently lose
        # the unpushed tail. A push after client disconnect raises
        # RuntimeError — swallow it and leave the unpushed line at the head
        # of the buffer. Retrying on the next tick is cheap, and on WebSocket
        # reconnect the push succeeds so the user sees a continuous stream
        # instead of a permanently frozen pane.
        while self._buf:
            line, css_class = self._buf[0]
            try:
                self._log.push(line, classes=css_class)
            except RuntimeError:
                return
            self._buf.popleft()
