"""Log viewer with line cap and per-line severity coloring."""

from __future__ import annotations

import re
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


class LogViewer:
    """Wraps ui.log with a line cap and severity-based coloring."""

    def __init__(self) -> None:
        self._log = ui.log(max_lines=MAX_LOG_LINES).classes("w-full h-96 theme-log")

    def append(self, line: str, severity: Severity | None = None) -> None:
        """Append a log line with optional explicit severity.

        Args:
            line: The raw log text (may contain newlines).
            severity: Explicit severity. When None, it is inferred from
                the line content via ``detect_severity``.
        """
        sev = severity if severity in _VALID_SEVERITIES else detect_severity(line)
        self._log.push(line, classes=f"log-line-{sev}")

    def append_stderr(self, line: str) -> None:
        """Append a stderr line (prefixed with ``[stderr]``)."""
        self._log.push(f"[stderr] {line}", classes="log-line-stderr")

    def clear(self) -> None:
        """Clear all log content."""
        self._log.clear()
