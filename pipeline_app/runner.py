"""Subprocess management for pipeline and tuning execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pipeline_app.components.log_viewer import MAX_LOG_LINES
from pipeline_app.config import (
    EnvSecrets,
    PipelineAppConfig,
    TuningConfig,
)

logger = logging.getLogger(__name__)

STAGE_MARKER_RE = re.compile(r"^##STAGE:(\w+)##\s*$")

PIPELINE_STAGES: list[str] = [
    "search",
    "retrieve",
    "extract",
    "validate",
    "merge",
    "sync",
]
TUNING_STAGES: list[str] = [
    "extract",
    "validate",
    "error_analysis",
    "calibrate",
    "track",
    "plot",
]
REPORT_DEPENDENT_STAGES: frozenset[str] = frozenset(
    {"validate", "error_analysis", "track"}
)
# Stages that consume the score distribution CSV written by error_analysis.
# Symmetric with REPORT_DEPENDENT_STAGES: missing input → clean "skipped",
# not a mid-command ValueError that surfaces as a confusing failure.
SCORE_DIST_DEPENDENT_STAGES: frozenset[str] = frozenset({"calibrate"})
RSCRIPT_EXE: str = "Rscript"

# Upper bound on a single log line from the subprocess. Asyncio's default
# StreamReader limit is 64 KiB; a single overflowing line raises
# LimitOverrunError from readline() and tears down both pipe readers
# mid-run. 10 MiB is enormously more than any legitimate pipeline log line
# (~<1 KiB typical) but small enough that a pathological run can't pin
# memory. _stream_lines also catches LimitOverrunError to keep going if an
# even larger line somehow appears.
_SUBPROCESS_STREAM_LIMIT: int = 10 * 1024 * 1024


# ---- Pure functions ----


def _int_str(value: int | float | None) -> str:
    """Convert a numeric value to integer string.

    NiceGUI's ui.number produces float values (e.g. 7.0) even for
    integer fields. Serializing directly with str() yields "7.0",
    which breaks argparse type=int and int() parsing downstream.

    Raises ValueError (not TypeError) on None so callers can surface a
    clean validation message; ui.number can momentarily hand back None
    when the user clears a field before entering a new value.
    """
    if value is None:
        raise ValueError("numeric field is required")
    return str(int(value))


_PYTHON_NAME_RE = re.compile(r"^python\d?(\.\d+)?$")
_PYTHON_NAME_HINT = "Must match 'python', 'python3', 'python3.x', etc."


def _resolve_executable(
    path_str: str,
    display_name: str,
    name_re: re.Pattern[str] | None = None,
    name_hint: str = "",
) -> str:
    """Resolve a bare command via PATH or validate an absolute/relative path."""
    if os.sep not in path_str:
        if name_re is not None and not name_re.match(path_str):
            raise ValueError(
                f"Invalid {display_name} executable name: {path_str!r}. "
                f"{name_hint}".rstrip()
            )
        resolved = shutil.which(path_str)
        if not resolved:
            raise ValueError(
                f"{display_name} executable not found in PATH: {path_str!r}"
            )
        return resolved

    # Preserve symlinks: venv python3.x is a symlink to the base interpreter,
    # and Python only enters venv mode when started via the symlink path
    # (so pyvenv.cfg and venv site-packages are discovered).
    resolved_path = Path(os.path.abspath(path_str))
    if not resolved_path.is_file():
        raise ValueError(f"{display_name} path does not exist: {path_str!r}")
    if not os.access(resolved_path, os.X_OK):
        raise ValueError(f"{display_name} path is not executable: {path_str!r}")
    if name_re is not None and not name_re.match(resolved_path.name):
        raise ValueError(
            f"Not a {display_name} interpreter: {resolved_path.name!r}. "
            f"{name_hint}".rstrip()
        )
    return str(resolved_path)


def validate_python_path(python_path: str) -> str:
    """Validate python_path is a Python interpreter (bare name or path)."""
    return _resolve_executable(
        python_path or "python3", "Python", _PYTHON_NAME_RE, _PYTHON_NAME_HINT
    )


def validate_rscript_path(rscript_path: str = RSCRIPT_EXE) -> str:
    """Resolve Rscript via PATH or absolute filesystem path.

    Resolving up front gives a clearer error than letting subprocess spawn
    raise FileNotFoundError mid-experiment.
    """
    return _resolve_executable(rscript_path or RSCRIPT_EXE, "Rscript")


def validate_project_root(project_root: str) -> str:
    """Validate that project_root is a legitimate project directory.

    Checks that the directory exists and contains ``pipeline/main.py``
    as a structural marker, preventing arbitrary directory traversal.

    Returns:
        The resolved absolute path to the project root.

    Raises:
        ValueError: If the path is not a valid project directory.
    """
    root = project_root or os.getcwd()
    resolved = Path(root).resolve()

    if not resolved.is_dir():
        raise ValueError(f"Project root is not a directory: {root!r}")
    if not (resolved / "pipeline" / "main.py").is_file():
        raise ValueError(
            f"Not a valid project directory (missing pipeline/main.py): {root!r}"
        )
    return str(resolved)


def resolve_project_root(project_root: str) -> Path:
    """Resolve project_root to an absolute Path, falling back to cwd.

    Unlike validate_project_root, no marker check — for read-only UI pages
    that should degrade gracefully when project_root is unconfigured.
    """
    return Path(project_root).resolve() if project_root else Path(os.getcwd()).resolve()


def get_project_anchor(config: PipelineAppConfig) -> Path | None:
    """Return config.project_root as a resolved anchor, or None if unset.

    Unlike resolve_project_root, returns None (not cwd) when project_root
    is empty so callers can gate picker dialogs on a user-configured root.
    """
    if not config.project_root:
        return None
    try:
        return Path(config.project_root).expanduser().resolve()
    except OSError, RuntimeError:
        return None


def build_cli_args(config: PipelineAppConfig) -> list[str]:
    """Build CLI arguments for pipeline/main.py."""
    args = ["pipeline/main.py"]
    if config.run_mode == "standard":
        if config.days_back is None or int(config.days_back) < 1:
            raise ValueError(f"days_back must be >= 1, got {config.days_back}")
        # Explicit --pubmed so sync_external_data is additive, not a replacement.
        args.append("--pubmed")
        args.extend(["--days-back", _int_str(config.days_back)])
        if config.dry_run:
            args.append("--dry-run")
        if config.test_mode:
            args.append("--test-mode")
        if config.sync_external_data:
            args.append("--sync-external-data")
    elif config.run_mode == "local_pdfs":
        if not config.local_pdfs_path:
            raise ValueError("local_pdfs_path must be set for local_pdfs run mode")
        args.extend(["--local-pdfs", config.local_pdfs_path])
        if config.skip_validation:
            args.append("--skip-validation")
    elif config.run_mode == "pmid_list":
        if not config.pmids_path:
            raise ValueError("pmids_path must be set for pmid_list run mode")
        args.extend(["--pmids", config.pmids_path])
        if config.skip_validation:
            args.append("--skip-validation")
    else:
        # Bare args fall through to pipeline/main.py's argparse default, which
        # would silently launch the full PubMed pipeline — refuse instead so
        # a hand-edited config.json or a future value can't trigger a real
        # production run unintentionally.
        raise ValueError(f"Unknown run_mode: {config.run_mode!r}")
    return args


def _base_env() -> dict[str, str]:
    """Build a minimal env with system essentials."""
    env: dict[str, str] = {"PYTHONUNBUFFERED": "1"}
    for key in ("PATH", "HOME"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, val in os.environ.items():
        if key.startswith("SSL_CERT_"):
            env[key] = val
    return env


@dataclass(slots=True, frozen=True)
class _EnvVarSpec:
    env_key: str
    attr: str
    formatter: Callable[[Any], str] = str
    include_empty: bool = True


_SECRET_ENV_SPECS: tuple[_EnvVarSpec, ...] = (
    _EnvVarSpec("ANTHROPIC_API_KEY", "anthropic_api_key", include_empty=False),
    _EnvVarSpec("DB_HOST", "db_host", include_empty=False),
    _EnvVarSpec("DB_PORT", "db_port", include_empty=False),
    _EnvVarSpec("DB_NAME", "db_name", include_empty=False),
    _EnvVarSpec("DB_USER", "db_user", include_empty=False),
    _EnvVarSpec("DB_PASSWORD", "db_password", include_empty=False),
    _EnvVarSpec("NCBI_API_KEY", "ncbi_api_key", include_empty=False),
    _EnvVarSpec("ENTREZ_EMAIL", "entrez_email", include_empty=False),
    _EnvVarSpec("UNPAYWALL_EMAIL", "unpaywall_email", include_empty=False),
)

_CONFIG_ENV_SPECS: tuple[_EnvVarSpec, ...] = (
    _EnvVarSpec("PIPELINE_LLM_PROVIDER", "llm_provider"),
    _EnvVarSpec("PIPELINE_LLM_MODEL", "llm_model"),
    _EnvVarSpec("PIPELINE_LLM_EFFORT", "llm_effort"),
    _EnvVarSpec("PIPELINE_LLM_MAX_TOKENS", "llm_max_tokens", _int_str),
    _EnvVarSpec("PIPELINE_PROMPT_VERSION", "prompt_version"),
    _EnvVarSpec("PIPELINE_CONFIDENCE_THRESHOLD", "confidence_threshold"),
    _EnvVarSpec("PIPELINE_MAX_CONCURRENT_PAPERS", "max_concurrent_papers", _int_str),
    _EnvVarSpec("PIPELINE_RPM_LIMIT", "rpm_limit", _int_str),
    _EnvVarSpec("PIPELINE_TPM_LIMIT", "tpm_limit", _int_str),
    _EnvVarSpec(
        "PIPELINE_ESTIMATED_TOKENS_PER_CALL",
        "estimated_tokens_per_call",
        _int_str,
    ),
    _EnvVarSpec("PIPELINE_NCBI_RATE_LIMIT", "ncbi_rate_limit", _int_str),
    _EnvVarSpec("PIPELINE_UNIPROT_RATE_LIMIT", "uniprot_rate_limit", _int_str),
    _EnvVarSpec("PIPELINE_MAX_PAPER_TEXT_CHARS", "max_paper_text_chars", _int_str),
    _EnvVarSpec("PIPELINE_MAX_RETRIES", "max_retries", _int_str),
    _EnvVarSpec(
        "PIPELINE_MAX_RATE_LIMIT_RETRIES",
        "max_rate_limit_retries",
        _int_str,
    ),
    _EnvVarSpec("PIPELINE_RATE_LIMIT_RETRY_DELAY", "rate_limit_retry_delay"),
    _EnvVarSpec(
        "PIPELINE_MAX_CONNECTION_RETRIES",
        "max_connection_retries",
        _int_str,
    ),
    _EnvVarSpec("PIPELINE_CONNECTION_RETRY_DELAY", "connection_retry_delay"),
    _EnvVarSpec("PIPELINE_DB_POOL_MIN", "db_pool_min_size", _int_str),
    _EnvVarSpec("PIPELINE_DB_POOL_MAX", "db_pool_max_size", _int_str),
    _EnvVarSpec("PIPELINE_DB_COMMAND_TIMEOUT", "db_command_timeout"),
    _EnvVarSpec("PIPELINE_PROGRESS_FILE", "progress_file", include_empty=False),
)


def _apply_env_specs(
    env: dict[str, str],
    source: object,
    specs: tuple[_EnvVarSpec, ...],
) -> None:
    """Copy object attributes into env according to spec metadata."""
    for spec in specs:
        value = getattr(source, spec.attr)
        if not spec.include_empty and not value:
            continue
        env[spec.env_key] = spec.formatter(value)


def build_env_vars(
    config: PipelineAppConfig,
    secrets: EnvSecrets,
) -> dict[str, str]:
    """Build the subprocess environment variables dict."""
    env = _base_env()

    # Skip empty-string secrets so the pipeline's os.environ.get()
    # fallbacks (and library-level "missing env var" errors) behave
    # normally instead of seeing "".
    _apply_env_specs(env, secrets, _SECRET_ENV_SPECS)
    _apply_env_specs(env, config, _CONFIG_ENV_SPECS)
    return env


async def _stream_lines(
    stream: asyncio.StreamReader,
    on_line: Callable[[str], None],
) -> None:
    """Read lines from an async stream, decode, and call back.

    Callback exceptions are logged and swallowed — propagating them would
    cancel the sibling pipe reader in ``_run_process_streamed`` and
    deadlock the subprocess on its next pipe write.

    LimitOverrunError (a line exceeding the stream's per-line limit) is
    caught and surfaced as a single truncation-notice callback. Without
    this, one pathological line would tear down both readers and leave
    the subprocess blocked on its next pipe write. CPython's readline()
    re-raises LimitOverrunError as ValueError — we handle both.

    ``readline()`` leaves the overflowing bytes in the StreamReader's
    buffer; without draining to the next newline, ``readline()`` re-raises
    on the same bytes forever. ``readuntil(b"\\n")`` advances cleanly to
    the next line boundary regardless of which exception variant fires.
    """
    while True:
        try:
            raw = await stream.readline()
        except asyncio.LimitOverrunError as exc:
            logger.warning("subprocess log line exceeded per-line limit: %s", exc)
            with suppress(Exception):
                on_line("[log-line truncated: exceeded per-line limit]")
            # Drain to the next newline so the next readline() starts on a
            # clean line boundary. IncompleteReadError fires at EOF; treat
            # that as end-of-stream by breaking out of the loop.
            try:
                await stream.readuntil(b"\n")
            except asyncio.IncompleteReadError:
                break
            except Exception:  # noqa: BLE001
                pass
            continue
        except ValueError as exc:
            # CPython occasionally re-raises LimitOverrunError as a plain
            # ValueError (no ``consumed`` attribute); we can't target a
            # precise drain, so read-and-discard up to the next newline.
            logger.warning("subprocess log line exceeded per-line limit: %s", exc)
            with suppress(Exception):
                on_line("[log-line truncated: exceeded per-line limit]")
            with suppress(Exception):
                await stream.readuntil(b"\n")
            continue
        if not raw:
            break
        # rstrip both \r and \n so CRLF lines from Windows-spawned subprocesses
        # don't leave a trailing \r that breaks the stage-marker regex.
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        try:
            on_line(line)
        except Exception:  # noqa: BLE001
            logger.exception("stream callback raised; continuing to drain")


async def _drain_streams(
    process: asyncio.subprocess.Process,
) -> None:
    """Drain stdout and stderr to EOF so the subprocess can exit cleanly.

    A SIGTERMed subprocess that still has buffered output will block on its
    next pipe write if we abandon the reader side, then refuse to exit.
    Reading both pipes to EOF unblocks it.
    """
    assert process.stdout is not None
    assert process.stderr is not None
    await asyncio.gather(
        process.stdout.read(),
        process.stderr.read(),
        return_exceptions=True,
    )


async def _run_process_streamed(
    process: asyncio.subprocess.Process,
    on_stdout: Callable[[str], None],
    on_stderr: Callable[[str], None],
) -> int:
    """Stream both pipes to completion, return exit code.

    On task cancellation, SIGTERM the subprocess group, drain pipes, wait
    up to 5s, then SIGKILL if the subprocess ignored SIGTERM. Without the
    escalation a stubborn child (e.g. custom signal handler) leaks into
    PID 1 after the parent exits (start_new_session=True).
    """
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        await asyncio.gather(
            _stream_lines(process.stdout, on_stdout),
            _stream_lines(process.stderr, on_stderr),
        )
        return await process.wait()
    except asyncio.CancelledError:
        # Only signal the child if it hasn't already exited. After exit, the
        # PID is eligible for reuse — signalling it risks hitting an
        # unrelated process. `cancel()` in SubprocessLock applies the same
        # guard; the two cancel paths can race via concurrent callers.
        if process.returncode is None:
            _signal_process_group(process, signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(_drain_streams(process), timeout=5.0)
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            if process.returncode is None:
                _signal_process_group(process, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=5.0)
        raise


def parse_stage_marker(line: str) -> str | None:
    """Extract stage name from ##STAGE:name##, or return None."""
    m = STAGE_MARKER_RE.match(line)
    return m.group(1) if m else None


# ---- File detection ----


def _find_newest_file(
    directory: Path,
    prefix: str,
    started_after: float,
) -> Path | None:
    """Find newest file with given prefix created after started_after.

    A TOCTOU between iterdir() yielding the entry and the stat() below can
    raise OSError if the pipeline rotated the file. Suppress per-entry so
    one transient failure doesn't lose the rest of the candidate set.
    """
    if not directory.is_dir():
        return None
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    candidates: list[tuple[Path, float]] = []
    for f in entries:
        if not f.name.startswith(prefix):
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime >= started_after - 1:
            candidates.append((f, mtime))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[1])[0]


def find_newest_report(
    logs_dir: Path,
    started_after: float,
) -> Path | None:
    """Find newest pipeline_report_*.json created after started_after."""
    return _find_newest_file(logs_dir / "json", "pipeline_report_", started_after)


# ---- Subprocess management ----


def _signal_process_group(
    proc: asyncio.subprocess.Process,
    sig: signal.Signals,
) -> None:
    """Send a signal to the subprocess — its whole group if it leads one.

    When the child was spawned with ``start_new_session=True`` it is its own
    process-group leader (pgid == pid), and signaling the group also reaps
    grandchildren. Otherwise (e.g. in tests that spawn plain subprocesses)
    we fall back to per-process ``terminate``/``kill`` so the signal does
    not reach siblings in the parent's group.
    """
    if hasattr(os, "killpg"):
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError, PermissionError:
            pgid = -1
        if pgid == proc.pid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, sig)
            return
    with contextlib.suppress(ProcessLookupError):
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()


@dataclass(slots=True)
class RunResult:
    """Result of a pipeline subprocess run."""

    exit_code: int
    report_path: str | None


class SubprocessLock:
    """Ensures only one subprocess runs at a time."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._is_running: bool = False
        # Set if cancel() arrives before set_process() — set_process will
        # honor it by terminating the just-spawned process.
        self._cancel_requested: bool = False
        # Signaled when set_process fires or when run_guard exits without
        # ever spawning. Lets cancel() block without polling.
        self._process_ready: asyncio.Event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._is_running

    def set_process(
        self,
        proc: asyncio.subprocess.Process | None,
    ) -> None:
        """Set the tracked subprocess (for cancellation)."""
        self._process = proc
        if proc is not None and self._cancel_requested:
            _signal_process_group(proc, signal.SIGTERM)
        if proc is not None:
            self._process_ready.set()

    @asynccontextmanager
    async def run_guard(self):
        """Acquire lock, track running state, and clean up on exit.

        Raises RuntimeError if another caller already holds the lock.
        The ``_is_running`` re-check inside the lock is defensive against
        a future await being introduced between locked() and acquire().
        """
        if self._lock.locked():
            raise RuntimeError("A process is already running")
        await self._lock.acquire()
        try:
            if self._is_running:
                raise RuntimeError("A process is already running")
            self._is_running = True
            self._cancel_requested = False
            self._process_ready.clear()
            try:
                yield
            finally:
                self._is_running = False
                self._process = None
                self._cancel_requested = False
                # Wake any cancel() awaiting the process so it sees the
                # cleared state and returns instead of waiting to the deadline.
                self._process_ready.set()
        finally:
            self._lock.release()

    async def cancel(self) -> None:
        """Send SIGTERM, then SIGKILL if still alive.

        Targets the child's process group when the child was spawned with
        ``start_new_session=True`` so grandchildren (e.g. asyncio subprocesses
        spawned by ``pipeline/main.py``) are also killed — otherwise they
        get reparented to PID 1 and keep consuming API quota / DB
        connections after cancel.
        """
        if not self._is_running:
            return
        proc = self._process
        if proc is None:
            # Subprocess hasn't been spawned yet (we're between
            # `_is_running = True` and `set_process(...)`). Flag the request
            # so set_process terminates the process as soon as it appears,
            # then await the ready Event (set by set_process or run_guard's
            # finally). Without the wait, shutdown handlers return before
            # cleanup actually happens and the subprocess orphans to PID 1.
            self._cancel_requested = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._process_ready.wait(), timeout=10.0)
            proc = self._process
            if proc is None:
                return
        _signal_process_group(proc, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            # Guard against racing run_guard's finally: if the proc exited
            # between the TimeoutError and here, sending SIGKILL to a PID
            # that may already have been recycled by the OS would kill an
            # unrelated process.
            if proc.returncode is None:
                _signal_process_group(proc, signal.SIGKILL)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except TimeoutError:
                    # Last-ditch direct kill so the proc isn't left as a
                    # permanent zombie if both signal-group attempts miss.
                    if proc.returncode is None:
                        with contextlib.suppress(ProcessLookupError, OSError):
                            proc.kill()


def _safe_call(cb: Callable[..., Any] | None, /, *args: Any) -> None:
    """Call ``cb(*args)``, silencing RuntimeError from disconnected clients.

    NiceGUI raises RuntimeError when a callback is bound to a page whose
    client has gone away. Swallowing it keeps the runner's reader loop
    intact so lines continue to buffer into ``log_lines`` for reconnect
    replay.
    """
    if cb is None:
        return
    with suppress(RuntimeError):
        cb(*args)


class _ListenerRegistry:
    """Manage callback bundles for reconnect-safe runner fan-out."""

    def __init__(self) -> None:
        self._bundles: list[Any] = []

    def add(self, bundle: Any) -> Callable[[], None]:
        self._bundles.append(bundle)

        def _dispose() -> None:
            with suppress(ValueError):
                self._bundles.remove(bundle)

        return _dispose

    def emit(self, callback_name: str, *args: Any) -> None:
        for bundle in list(self._bundles):
            _safe_call(getattr(bundle, callback_name), *args)


@dataclass(slots=True)
class PipelineListeners:
    """A single client's set of PipelineRunner callbacks.

    Stored as one entry in PipelineRunner._listeners so multiple concurrent
    browser clients each get their own log/tracker updates during a run.
    """

    on_stdout: Callable[[str], None]
    on_stderr: Callable[[str], None]
    on_stage: Callable[[str], None]


@dataclass(slots=True)
class TuningListeners:
    """A single client's set of TuningRunner callbacks."""

    on_stdout: Callable[[str], None]
    on_stderr: Callable[[str], None]
    on_stage_start: Callable[[str, int, int], None]
    on_stage_complete: Callable[[str, list[Path]], None]
    on_waiting: Callable[[], None]


class PipelineRunner:
    """Spawns pipeline/main.py and streams output.

    Buffers log lines, stage status, and the last result so a UI page that
    reconnects after navigating away mid-run picks up the run in flight
    without losing the stream or the tracker state. Mirrors
    ``TuningRunner``'s add_listener / buffered-state pattern so
    ``configure_run.py`` and ``tuning.py`` stay structurally symmetric.
    """

    def __init__(self, lock: SubprocessLock) -> None:
        self._lock = lock
        # Buffered run state (persists across page navigations). deque with
        # maxlen auto-evicts the oldest on overflow so reconnect replay
        # matches the UI's ui.log rotation window.
        self.log_lines: deque[tuple[str, str]] = deque(maxlen=MAX_LOG_LINES)
        self.stage_statuses: dict[str, str] = {s: "pending" for s in PIPELINE_STAGES}
        self._current_stage: str | None = None
        self.last_result: RunResult | None = None
        # Supports multiple concurrent browser clients: each page mount
        # appends a listener bundle, each client-disconnect pops one.
        self._listeners = _ListenerRegistry()

    def add_listener(
        self,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
        on_stage: Callable[[str], None],
    ) -> Callable[[], None]:
        """Register a listener bundle; returns a disposer to remove it.

        Pages should call this once per client mount and wire the disposer
        to ``context.client.on_disconnect`` so callbacks bound to closed
        sessions don't accumulate across a long-running server.
        """
        return self._listeners.add(PipelineListeners(on_stdout, on_stderr, on_stage))

    def reset_state(self) -> None:
        """Clear buffered run state so a fresh run renders from zero."""
        self.log_lines.clear()
        self.stage_statuses = {s: "pending" for s in PIPELINE_STAGES}
        self._current_stage = None
        self.last_result = None

    def _emit_stdout(self, line: str) -> None:
        self.log_lines.append(("out", line))
        self._listeners.emit("on_stdout", line)

    def _emit_stderr(self, line: str) -> None:
        self.log_lines.append(("err", line))
        self._listeners.emit("on_stderr", line)

    def _emit_stage(self, stage: str) -> None:
        # Close out the previous stage before marking the new one running,
        # so the tracker shows a clean "completed → running" transition
        # even if the caller never sees individual markers live.
        prev = self._current_stage
        if prev is not None and self.stage_statuses.get(prev) == "running":
            self.stage_statuses[prev] = "completed"
        self._current_stage = stage
        self.stage_statuses[stage] = "running"
        self._listeners.emit("on_stage", stage)

    async def run(
        self,
        config: PipelineAppConfig,
        secrets: EnvSecrets,
        cli_args_override: list[str] | None = None,
    ) -> RunResult:
        """Run the pipeline subprocess and stream output into buffered state.

        Listener callbacks are registered with ``add_listener`` before ``run``.
        Lines are appended to ``log_lines`` first and forwarded to UI
        callbacks second, so a disconnected client can't drop buffered data.

        Args:
            cli_args_override: Use these args instead of build_cli_args.
                For testing with arbitrary scripts.
        """
        if self._lock.is_running:
            raise RuntimeError("A process is already running")

        # Validate before resetting state so a bad python_path / project_root
        # raises cleanly without wiping the previous run's log buffer and
        # last_result — otherwise the UI shows a blank tracker and "no last
        # result" that's indistinguishable from a never-run state.
        validated_python = validate_python_path(config.python_path)
        project_root = validate_project_root(config.project_root)
        args = cli_args_override or build_cli_args(config)
        env = build_env_vars(config, secrets)

        self.reset_state()
        started_at = time.time()

        def _handle_stdout(line: str) -> None:
            stage = parse_stage_marker(line)
            if stage:
                self._emit_stage(stage)
            else:
                self._emit_stdout(line)

        async with self._lock.run_guard():
            try:
                process = await asyncio.create_subprocess_exec(
                    validated_python,
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=project_root,
                    env=env,
                    start_new_session=True,
                    limit=_SUBPROCESS_STREAM_LIMIT,
                )
            except OSError as spawn_exc:
                # fd exhaustion, permission, or missing interpreter after
                # PATH resolution — surface as a failed first stage instead
                # of leaving the tracker in "all pending" with no last_result.
                self._emit_stderr(f"Failed to start subprocess: {spawn_exc}")
                if PIPELINE_STAGES:
                    self.stage_statuses[PIPELINE_STAGES[0]] = "failed"
                result = RunResult(exit_code=-1, report_path=None)
                self.last_result = result
                return result
            self._lock.set_process(process)

            try:
                exit_code = await _run_process_streamed(
                    process, _handle_stdout, self._emit_stderr
                )
            except asyncio.CancelledError:
                # A reconnecting page would otherwise see the last stage
                # frozen on "running" forever; mark it failed and record a
                # cancelled result before letting the cancellation propagate.
                # Clear _current_stage so a UI refresh between cancel and
                # the next run() call doesn't observe a stale stage name.
                if self._current_stage is not None:
                    self.stage_statuses[self._current_stage] = "failed"
                    self._current_stage = None
                self.last_result = RunResult(exit_code=-1, report_path=None)
                raise

            # Terminal state for the last stage: if at least one stage ran,
            # finalize it; otherwise if we exited with a failure code,
            # paint the first stage failed so a reconnecting page sees a
            # diagnostic tracker instead of "all pending".
            if self._current_stage is not None:
                final_status = "completed" if exit_code == 0 else "failed"
                self.stage_statuses[self._current_stage] = final_status
            elif exit_code != 0 and PIPELINE_STAGES:
                self.stage_statuses[PIPELINE_STAGES[0]] = "failed"

            logs_dir = Path(project_root) / "logs"
            report = find_newest_report(logs_dir, started_at)

            result = RunResult(
                exit_code=exit_code,
                report_path=str(report) if report else None,
            )
            self.last_result = result
            return result


# ---- Tuning ----


def build_extract_config(
    main_config: PipelineAppConfig,
    tuning: TuningConfig,
) -> PipelineAppConfig:
    """Build a PipelineAppConfig for the tuning Extract stage."""
    overrides: dict[str, Any] = {
        "run_mode": "local_pdfs",
        "local_pdfs_path": tuning.pdf_path,
        "skip_validation": True,
        "dry_run": True,
        "confidence_threshold": tuning.confidence_threshold,
    }
    if not tuning.use_main_config:
        overrides.update(
            {
                "python_path": tuning.python_path,
                "project_root": tuning.project_root,
                "llm_provider": tuning.llm_provider,
                "llm_model": tuning.llm_model,
                "llm_effort": tuning.llm_effort,
                "llm_max_tokens": tuning.llm_max_tokens,
                "prompt_version": tuning.prompt_version,
            }
        )
    return replace(main_config, **overrides)


def get_tuning_project_root(
    main_config: PipelineAppConfig,
    tuning: TuningConfig,
) -> str:
    """Return the project root the tuning run should execute under.

    ``use_main_config=False`` allows a tuning config to override the project
    root, but an empty override is treated as "not configured" and falls back
    to the main config. This matches the UI, which currently exposes LLM
    overrides but not separate environment fields.
    """
    if not tuning.use_main_config and tuning.project_root:
        return tuning.project_root
    return main_config.project_root


def get_tuning_python_path(
    main_config: PipelineAppConfig,
    tuning: TuningConfig,
) -> str:
    """Return the Python interpreter the tuning run should use."""
    if not tuning.use_main_config and tuning.python_path:
        return tuning.python_path
    return main_config.python_path


def _extract_stage_args(
    tuning: TuningConfig,
    _report_path: str | None,
    _score_dist_path: str | None,
    _run_group: str,
) -> list[str]:
    return [
        "pipeline/main.py",
        "--local-pdfs",
        tuning.pdf_path,
        "--skip-validation",
        "--dry-run",
    ]


def _validate_stage_args(
    tuning: TuningConfig,
    report_path: str | None,
    _score_dist_path: str | None,
    _run_group: str,
) -> list[str]:
    if not report_path:
        raise ValueError("validate stage requires report_path from extract")
    return [
        "scripts/validate_pipeline.py",
        report_path,
        "--reference",
        tuning.gold_standard_path,
        "--local-pdfs",
    ]


def _error_analysis_stage_args(
    tuning: TuningConfig,
    report_path: str | None,
    _score_dist_path: str | None,
    _run_group: str,
) -> list[str]:
    if not report_path:
        raise ValueError("error_analysis stage requires report_path from extract")
    return [
        "scripts/tuning/analyze_errors.py",
        report_path,
        "--reference",
        tuning.gold_standard_path,
        "--local-pdfs",
    ]


def _calibrate_stage_args(
    tuning: TuningConfig,
    _report_path: str | None,
    score_dist_path: str | None,
    _run_group: str,
) -> list[str]:
    if not score_dist_path:
        raise ValueError("calibrate stage requires score_dist_path from error_analysis")
    return [
        "scripts/tuning/calibrate_threshold.py",
        score_dist_path,
        "--beta",
        str(tuning.f_beta_weight),
    ]


def _track_stage_args(
    tuning: TuningConfig,
    report_path: str | None,
    _score_dist_path: str | None,
    run_group: str,
) -> list[str]:
    if not report_path:
        raise ValueError("track stage requires report_path from extract")
    args = [
        "scripts/tuning/track_run.py",
        "--pipeline-report",
        report_path,
        "--reference",
        tuning.gold_standard_path,
        "--local-pdfs",
    ]
    if tuning.notes:
        args.extend(["--notes", tuning.notes.replace("\r", " ").replace("\n", " ")])
    if run_group:
        args.extend(["--run-group", run_group])
    return args


def _plot_stage_args(
    _tuning: TuningConfig,
    _report_path: str | None,
    _score_dist_path: str | None,
    _run_group: str,
) -> list[str]:
    return ["scripts/plot_tuning_runs.R"]


@dataclass(slots=True, frozen=True)
class _TuningStageSpec:
    executable: str
    build_args: Callable[[TuningConfig, str | None, str | None, str], list[str]]


_TUNING_STAGE_SPECS: dict[str, _TuningStageSpec] = {
    "extract": _TuningStageSpec("python", _extract_stage_args),
    "validate": _TuningStageSpec("python", _validate_stage_args),
    "error_analysis": _TuningStageSpec("python", _error_analysis_stage_args),
    "calibrate": _TuningStageSpec("python", _calibrate_stage_args),
    "track": _TuningStageSpec("python", _track_stage_args),
    "plot": _TuningStageSpec(RSCRIPT_EXE, _plot_stage_args),
}


def build_tuning_stage_command(
    stage: str,
    tuning: TuningConfig,
    report_path: str | None = None,
    score_dist_path: str | None = None,
    run_group: str = "",
) -> tuple[str, list[str]]:
    """Return (executable, args) for a tuning stage subprocess."""
    spec = _TUNING_STAGE_SPECS.get(stage)
    if spec is None:
        raise ValueError(f"Unknown stage: {stage}")
    exe = (
        (tuning.python_path or "python3")
        if spec.executable == "python"
        else RSCRIPT_EXE
    )
    return exe, spec.build_args(tuning, report_path, score_dist_path, run_group)


def _collect_tuning_stage_outputs(
    stage: str,
    logs_dir: Path,
    started_at: float,
) -> tuple[list[Path], str | None, str | None]:
    """Return output files plus newly discovered report/score paths."""
    if stage == "extract":
        report = find_newest_report(logs_dir, started_at)
        return ([report] if report else []), str(report) if report else None, None

    if stage == "error_analysis":
        output_files: list[Path] = []
        score_dist_path: str | None = None
        score_dist = _find_newest_file(
            logs_dir / "tuning" / "score_distributions",
            "score_distribution_",
            started_at,
        )
        if score_dist:
            score_dist_path = str(score_dist)
            output_files.append(score_dist)
        error_analysis = _find_newest_file(
            logs_dir / "tuning" / "error_analyses",
            "error_analysis_",
            started_at,
        )
        if error_analysis:
            output_files.append(error_analysis)
        return output_files, None, score_dist_path

    if stage == "calibrate":
        curve = _find_newest_file(
            logs_dir / "png" / "pr_curves",
            "pr_curve_",
            started_at,
        )
        return ([curve] if curve else []), None, None

    return [], None, None


class TuningRunner:
    """Orchestrates the multi-stage tuning workflow.

    Buffers log output and stage statuses so the UI can reconnect
    after page navigation without losing progress.
    """

    def __init__(self, lock: SubprocessLock) -> None:
        self._lock = lock
        self._advance_event: asyncio.Event | None = None
        self._skip_next: bool = False
        self._cancelled: bool = False
        self._is_waiting: bool = False
        # Buffered run state (persists across page navigations). A
        # deque(maxlen=...) so the oldest lines are evicted instead of the
        # newest being silently dropped — matches the UI ui.log rotation.
        self.log_lines: deque[tuple[str, str]] = deque(maxlen=MAX_LOG_LINES)
        self.stage_statuses: dict[str, str] = {s: "pending" for s in TUNING_STAGES}
        self.stage_durations: dict[str, float] = {}
        self._stage_started_at: dict[str, float] = {}
        self.current_repeat: int = 0
        self.total_repeats: int = 0
        # Listener bundles keyed by insertion order. See PipelineRunner for
        # the multi-client rationale.
        self._listeners = _ListenerRegistry()

    @property
    def is_active(self) -> bool:
        """True while ``run_experiment`` is mid-flight (not just spawned)."""
        return self._advance_event is not None

    @property
    def is_waiting(self) -> bool:
        """True while paused between stages, awaiting user input."""
        return self._is_waiting

    @property
    def was_cancelled(self) -> bool:
        """True if the last run_experiment ended via ``cancel()``.

        Latches True on ``cancel()`` and resets to False at the top of the
        next ``run_experiment``; lets the UI distinguish a user cancel from
        a clean finish when both return normally.
        """
        return self._cancelled

    @property
    def any_running(self) -> bool:
        """True if a tuning experiment OR plain pipeline subprocess is active.

        The sidebar Cancel button binds to this so the button stays visible
        during inter-stage waits in tuning (when the subprocess lock is
        momentarily released) as well as during plain pipeline runs.
        """
        return self.is_active or self._lock.is_running

    def add_listener(
        self,
        on_stdout: Callable[[str], None],
        on_stderr: Callable[[str], None],
        on_stage_start: Callable[[str, int, int], None],
        on_stage_complete: Callable[[str, list[Path]], None],
        on_waiting: Callable[[], None],
    ) -> Callable[[], None]:
        """Register a listener bundle; returns a disposer to remove it."""
        return self._listeners.add(
            TuningListeners(
                on_stdout,
                on_stderr,
                on_stage_start,
                on_stage_complete,
                on_waiting,
            )
        )

    def reset_state(self) -> None:
        """Clear buffered run state so the UI tracker renders fresh."""
        self.log_lines.clear()
        self.stage_statuses = {s: "pending" for s in TUNING_STAGES}
        self.stage_durations = {}
        self._stage_started_at = {}
        self.current_repeat = 0
        self.total_repeats = 0
        # Clear any stale skip request so it can't leak into the first
        # inter-stage wait of the next experiment.
        self._skip_next = False

    def _emit_stdout(self, line: str) -> None:
        self.log_lines.append(("out", line))
        self._listeners.emit("on_stdout", line)

    def _emit_stderr(self, line: str) -> None:
        self.log_lines.append(("err", line))
        self._listeners.emit("on_stderr", line)

    def _emit_stage_start(
        self,
        stage: str,
        repeat: int,
        total: int,
    ) -> None:
        self.stage_statuses[stage] = "running"
        self._stage_started_at[stage] = time.monotonic()
        self.current_repeat = repeat
        self.total_repeats = total
        self._listeners.emit("on_stage_start", stage, repeat, total)

    def _emit_stage_complete(
        self,
        stage: str,
        files: list[Path],
        status: str = "completed",
    ) -> None:
        self.stage_statuses[stage] = status
        started = self._stage_started_at.pop(stage, None)
        if started is not None:
            self.stage_durations[stage] = time.monotonic() - started
        self._listeners.emit("on_stage_complete", stage, files)

    def _emit_waiting(self) -> None:
        self._listeners.emit("on_waiting")

    def advance(self) -> None:
        """User clicked Next Stage."""
        self._skip_next = False
        if self._advance_event is not None:
            self._advance_event.set()

    def skip(self) -> None:
        """User clicked Skip Stage."""
        self._skip_next = True
        if self._advance_event is not None:
            self._advance_event.set()

    async def cancel(self) -> None:
        """User clicked Cancel."""
        self._cancelled = True
        if self._advance_event is not None:
            self._advance_event.set()
        await self._lock.cancel()

    async def run_experiment(
        self,
        config: PipelineAppConfig,
        tuning: TuningConfig,
        secrets: EnvSecrets,
        script_override: str | None = None,
    ) -> None:
        """Run the full tuning experiment (possibly multi-repeat)."""
        # Setup is inside try/finally so validation errors still clear
        # _advance_event — a leaked Event keeps is_active True and the
        # sidebar Cancel button wired to a dead run.
        try:
            self._cancelled = False
            self._is_waiting = False
            self._advance_event = asyncio.Event()

            # Validate before reset_state so a bad repeats / python_path /
            # project_root raises without wiping the previous experiment's
            # buffered log lines and stage statuses from the UI.
            total_repeats = int(tuning.repeats)
            if total_repeats < 1:
                raise ValueError(f"tuning.repeats must be >= 1, got {total_repeats}")
            run_group = time.strftime("%Y%m%d_%H%M%S") if total_repeats > 1 else ""
            project_root = validate_project_root(
                get_tuning_project_root(config, tuning)
            )
            logs_dir = Path(project_root) / "logs"
            validated_python = validate_python_path(
                get_tuning_python_path(config, tuning)
            )
            stage_env_config = (
                config
                if tuning.use_main_config
                else build_extract_config(config, tuning)
            )

            self.reset_state()
            # Resolved lazily — only when a plot stage actually runs, so users
            # who never reach plot don't need R installed up front.
            validated_rscript: str | None = None
            for repeat in range(1, total_repeats + 1):
                if self._cancelled:
                    break

                self.stage_statuses = {s: "pending" for s in TUNING_STAGES}
                # Drop stale start-times from the previous repeat so a stage
                # that aborted without _emit_stage_complete doesn't leak its
                # start time into the next repeat's duration calculation.
                self._stage_started_at.clear()
                report_path: str | None = None
                score_dist_path: str | None = None

                for i, stage in enumerate(TUNING_STAGES):
                    if self._cancelled:
                        break

                    needs_report = stage in REPORT_DEPENDENT_STAGES
                    if needs_report and not report_path and not script_override:
                        self._emit_stderr(
                            f"Skipping {stage}: no report from extract stage"
                        )
                        self._emit_stage_complete(stage, [], status="skipped")
                        continue

                    needs_score_dist = stage in SCORE_DIST_DEPENDENT_STAGES
                    if needs_score_dist and not score_dist_path and not script_override:
                        self._emit_stderr(
                            f"Skipping {stage}: "
                            "no score distribution from error_analysis stage"
                        )
                        self._emit_stage_complete(stage, [], status="skipped")
                        continue

                    self._emit_stage_start(stage, repeat, total_repeats)
                    started_at = time.time()

                    try:
                        if script_override:
                            exe = validated_python
                            args = [script_override]
                            env = _base_env()
                        else:
                            if stage == "extract":
                                extract_cfg = build_extract_config(
                                    config,
                                    tuning,
                                )
                                exe = validated_python
                                args = build_cli_args(extract_cfg)
                                env = build_env_vars(extract_cfg, secrets)
                            else:
                                exe, args = build_tuning_stage_command(
                                    stage,
                                    tuning,
                                    report_path=report_path,
                                    score_dist_path=score_dist_path,
                                    run_group=run_group,
                                )
                                if exe == RSCRIPT_EXE:
                                    if validated_rscript is None:
                                        validated_rscript = validate_rscript_path()
                                    exe = validated_rscript
                                    # plot is pure-compute; no secrets needed.
                                    env = _base_env()
                                else:
                                    # Honor tuning environment overrides for
                                    # non-extract Python stages too, matching
                                    # the extract branch above.
                                    exe = validated_python
                                    # Non-extract Python stages need DB
                                    # creds, NCBI keys, and PIPELINE_* knobs.
                                    env = build_env_vars(stage_env_config, secrets)

                        async with self._lock.run_guard():
                            process = await asyncio.create_subprocess_exec(
                                exe,
                                *args,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                                cwd=project_root,
                                env=env,
                                start_new_session=True,
                                limit=_SUBPROCESS_STREAM_LIMIT,
                            )
                            self._lock.set_process(process)

                            exit_code = await _run_process_streamed(
                                process,
                                self._emit_stdout,
                                self._emit_stderr,
                            )
                    except (
                        OSError,
                        ValueError,
                        RuntimeError,
                    ) as e:
                        # OSError covers the full family of spawn failures
                        # (missing interpreter, permission, fd exhaustion,
                        # fork failure); without the parent class, EMFILE /
                        # ENOMEM escape silently and the stage flips to
                        # failed with no diagnostic line in the log pane.
                        # RuntimeError covers run_guard's "already running"
                        # check — without it the stage tracker would stay
                        # stuck in "running" and the experiment would abort
                        # silently after the first stage.
                        self._emit_stderr(f"Stage {stage} failed to start: {e}")
                        self._emit_stage_complete(stage, [], status="failed")
                    else:
                        # If the user cancelled while the subprocess was live,
                        # its exit_code reflects the signal (not a clean run).
                        # Mark the stage cancelled and break out of the loop
                        # — don't scrape partial artifacts and populate
                        # report_path from a killed extract stage, or the
                        # next stage's report_path check would silently use
                        # corrupt data.
                        if self._cancelled:
                            self._emit_stage_complete(stage, [], status="cancelled")
                            break
                        # Non-zero exit is a stage failure. Downstream stages
                        # that need a report file are guarded by needs_report
                        # and self-skip when report_path stays None.
                        if exit_code != 0:
                            self._emit_stderr(
                                f"Stage {stage} exited with code {exit_code}"
                            )
                            self._emit_stage_complete(stage, [], status="failed")
                        else:
                            (
                                output_files,
                                new_report_path,
                                new_score_dist_path,
                            ) = _collect_tuning_stage_outputs(
                                stage,
                                logs_dir,
                                started_at,
                            )
                            report_path = new_report_path or report_path
                            score_dist_path = new_score_dist_path or score_dist_path
                            self._emit_stage_complete(stage, output_files)

                    # Wait for user between stages (unless last or auto)
                    is_last = i == len(TUNING_STAGES) - 1
                    if not is_last and not tuning.auto_advance and not self._cancelled:
                        # Fresh Event each wait window: any advance/skip/cancel
                        # that arrived between stages still calls .set() on the
                        # current event reference, so swapping in a new one
                        # discards stale signals and avoids the clear-then-wait
                        # race where a click between clear() and wait() is lost.
                        self._advance_event = asyncio.Event()
                        self._is_waiting = True
                        try:
                            self._emit_waiting()
                            await self._advance_event.wait()
                        finally:
                            self._is_waiting = False

                        if self._cancelled:
                            break
                        if self._skip_next:
                            self._skip_next = False
                            continue
        finally:
            # Mark any mid-flight stage failed so a reconnect doesn't see a
            # ghost "running" status after CancelledError or any exception
            # the inner per-stage except didn't cover.
            for _stage, _status in self.stage_statuses.items():
                if _status == "running":
                    self.stage_statuses[_stage] = "failed"
            self._advance_event = None
            self._is_waiting = False
