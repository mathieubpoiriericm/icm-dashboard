"""Log viewer with line cap and per-line severity coloring."""

from __future__ import annotations

import re
from collections import deque
from typing import Literal, get_args

from nicegui import ui

MAX_LOG_LINES: int = 10_000

Severity = Literal["info", "warn", "error", "stderr", "debug"]

_VALID_SEVERITIES: frozenset[str] = frozenset(get_args(Severity))

# Ordered: first match wins. Bracketed tokens are checked too because many
# loggers emit lines like "[ERROR] something failed".
_SEVERITY_PATTERNS: tuple[tuple[re.Pattern[str], Severity], ...] = (
    (
        re.compile(r"\[?\bERROR\b\]?|\bCRITICAL\b|\bFATAL\b|Traceback", re.IGNORECASE),
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
        ui.timer(_FLUSH_INTERVAL_S, self._flush)

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

    def _flush(self) -> None:
        if not self._buf:
            return
        batch = list(self._buf)
        self._buf.clear()
        for line, css_class in batch:
            self._log.push(line, classes=css_class)
