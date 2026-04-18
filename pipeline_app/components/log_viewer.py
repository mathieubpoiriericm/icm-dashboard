"""Log viewer component with line cap and stderr coloring."""

from __future__ import annotations

from nicegui import ui

MAX_LOG_LINES: int = 10_000


class LogViewer:
    """Wraps ui.log with a line count cap."""

    def __init__(self) -> None:
        self._log = ui.log(max_lines=MAX_LOG_LINES).classes("w-full h-96 theme-log")

    def append(self, line: str) -> None:
        """Append a stdout line."""
        self._log.push(line)

    def append_stderr(self, line: str) -> None:
        """Append a stderr line (prefixed with [stderr])."""
        self._log.push(f"[stderr] {line}")

    def clear(self) -> None:
        """Clear all log content."""
        self._log.clear()
