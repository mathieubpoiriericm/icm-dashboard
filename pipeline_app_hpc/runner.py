"""Subprocess management for HPC vLLM pipeline and tuning execution."""

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
from typing import TYPE_CHECKING, Any

from pipeline_app.components.log_viewer import MAX_LOG_LINES

if TYPE_CHECKING:
    from pipeline_app_hpc.config import EnvSecrets, HpcAppConfig, TuningConfig
    from pipeline_app_hpc.hpc.lifecycle import VllmServer

logger = logging.getLogger(__name__)

STAGE_MARKER_RE = re.compile(r"^##STAGE:(\w+)##\s*$")

PIPELINE_STAGES: list[str] = [
    "extract",
    "batch_validate",
    "report",
]

TUNING_STAGES: list[str] = [
    "extract",
    "validate",
    "error_analysis",
    "calibrate",
    "track",
    "plot",
]

# Upper bound on a single log line from the subprocess.
_SUBPROCESS_STREAM_LIMIT: int = 10 * 1024 * 1024

_PYTHON_NAME_RE = re.compile(r"^python\d?(\.\d+)?$")
_PYTHON_NAME_HINT = "Must match 'python', 'python3', 'python3.x', etc."
_STATUS_PENDING = "pending"
_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"


# ---- Process helpers ----


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


def validate_project_root(project_root: str) -> str:
    """Validate that project_root is a legitimate project directory.

    Checks that the directory exists and contains ``pipeline/main.py``
    as a structural marker.

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


def parse_stage_marker(line: str) -> str | None:
    """Extract stage name from ##STAGE:name##, or return None."""
    m = STAGE_MARKER_RE.match(line)
    return m.group(1) if m else None


def _pending_stage_statuses(stages: list[str]) -> dict[str, str]:
    """Return a fresh pending-status dict for a stage sequence."""
    return {stage: _STATUS_PENDING for stage in stages}


def _advance_stage(
    stage_statuses: dict[str, str],
    current_stage: str | None,
    stage: str,
) -> str | None:
    """Mark ``stage`` running and close the previous running stage."""
    if stage not in stage_statuses:
        return current_stage
    if (
        current_stage is not None
        and stage_statuses.get(current_stage) == _STATUS_RUNNING
    ):
        stage_statuses[current_stage] = _STATUS_COMPLETED
    stage_statuses[stage] = _STATUS_RUNNING
    return stage


def _final_stage_status(exit_code: int) -> str:
    """Return the terminal stage status for a subprocess exit code."""
    return _STATUS_COMPLETED if exit_code == 0 else _STATUS_FAILED


def _find_newest_file(
    directory: Path,
    prefix: str,
    started_after: float,
) -> Path | None:
    """Find newest file with given prefix created after started_after."""
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


def _signal_process_group(
    proc: asyncio.subprocess.Process,
    sig: signal.Signals,
) -> None:
    """Send a signal to the subprocess — its whole group if it leads one.

    Falls through to a direct signal on the process when the group kill
    fails (transient race or restricted permission); without the fallback
    a failed killpg leaves the process unsignaled.
    """
    if hasattr(os, "killpg"):
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pgid = -1
        if pgid == proc.pid:
            try:
                os.killpg(pgid, sig)
                return
            except (ProcessLookupError, PermissionError):
                pass
    with contextlib.suppress(ProcessLookupError):
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()


async def _stream_lines(
    stream: asyncio.StreamReader,
    on_line: Callable[[str], None],
) -> None:
    """Read lines from an async stream, decode, and call back."""

    async def _report_truncated_line(exc: BaseException) -> None:
        logger.warning("subprocess log line exceeded per-line limit: %s", exc)
        with suppress(Exception):
            on_line("[log-line truncated: exceeded per-line limit]")
        try:
            await stream.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            raise
        except Exception:  # noqa: BLE001
            pass

    while True:
        try:
            raw = await stream.readline()
        except asyncio.LimitOverrunError as exc:
            try:
                await _report_truncated_line(exc)
            except asyncio.IncompleteReadError:
                break
            continue
        except ValueError as exc:
            await _report_truncated_line(exc)
            continue
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        try:
            on_line(line)
        except Exception:  # noqa: BLE001
            logger.exception("stream callback raised; continuing to drain")


async def _drain_streams(
    process: asyncio.subprocess.Process,
) -> None:
    """Drain stdout and stderr to EOF so the subprocess can exit cleanly."""
    assert process.stdout is not None
    assert process.stderr is not None
    await asyncio.gather(
        process.stdout.read(),
        process.stderr.read(),
        return_exceptions=True,
    )


# ---- Core data types ----


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
        self._cancel_requested: bool = False
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
        """Acquire lock, track running state, and clean up on exit."""
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
                self._process_ready.set()
        finally:
            self._lock.release()

    async def cancel(self) -> None:
        """Send SIGTERM, then SIGKILL if still alive."""
        if not self._is_running:
            return
        proc = self._process
        if proc is None:
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
            if proc.returncode is None:
                _signal_process_group(proc, signal.SIGKILL)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except TimeoutError:
                    if proc.returncode is None:
                        with contextlib.suppress(ProcessLookupError, OSError):
                            proc.kill()


def _safe_call(cb: Callable[..., Any] | None, /, *args: Any) -> None:
    """Call ``cb(*args)``, silencing RuntimeError from disconnected clients."""
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
    """A single client's set of PipelineRunner callbacks."""

    on_stdout: Callable[[str], None]
    on_stderr: Callable[[str], None]
    on_stage: Callable[[str], None]


# ---- Env building ----


def _base_env() -> dict[str, str]:
    """Allowlisted base env passed to the subprocess."""
    keys = ("PATH", "HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG", "LC_ALL")
    base = {k: os.environ[k] for k in keys if k in os.environ}
    # Without this, subprocess stdout is block-buffered against a pipe and
    # the live log pane stalls until 8 KiB accumulates, then dumps in bursts.
    base["PYTHONUNBUFFERED"] = "1"
    return base


def build_extract_args(config: HpcAppConfig) -> list[str]:
    """Build CLI args for `python -m pipeline_app_hpc.cli`."""
    if not config.local_pdfs_path:
        raise ValueError("local_pdfs_path is required")
    args = ["--local-pdfs", config.local_pdfs_path]
    if config.skip_validation:
        args.append("--skip-validation")
    return args


def build_extract_env(
    config: HpcAppConfig,
    secrets: EnvSecrets,
    local_url: str,
) -> dict[str, str]:
    """Build the env dict for the cli subprocess. Allowlist + VLLM_/PIPELINE_ vars.

    The PIPELINE_* vars set here are consumed directly by ``PipelineConfig``'s
    ``field(default_factory=lambda: _env_*("PIPELINE_*", default))`` lambdas
    in the subprocess — there is no parallel override table on the cli.py
    side. Adding a new tunable to ``HpcAppConfig`` therefore requires both
    a corresponding entry here and a matching field in ``PipelineConfig``.
    """
    base = _base_env()
    # vLLM endpoint + model (adapter alias if set, else base name)
    model = (
        config.vllm_adapter_name if config.vllm_adapter_path else config.vllm_base_model
    )
    base.update(
        {
            "VLLM_BASE_URL": local_url,
            "VLLM_MODEL": model,
            "VLLM_BASE_MODEL_NAME": config.vllm_base_model,
            "VLLM_ADAPTER_NAME": (
                config.vllm_adapter_name if config.vllm_adapter_path else ""
            ),
            "VLLM_MAX_MODEL_LEN": str(config.vllm_max_model_len),
            "VLLM_QUANTIZATION": config.vllm_quantization,
            "PIPELINE_CONFIDENCE_THRESHOLD": str(config.confidence_threshold),
            "PIPELINE_MAX_PAPER_TEXT_CHARS": str(config.max_paper_text_chars),
            "PIPELINE_MAX_CONCURRENT_PAPERS": str(config.max_concurrent_papers),
            "PIPELINE_RPM_LIMIT": str(config.rpm_limit),
            "PIPELINE_TPM_LIMIT": str(config.tpm_limit),
            "PIPELINE_ESTIMATED_TOKENS_PER_CALL": str(
                config.estimated_tokens_per_call
            ),
            "PIPELINE_NCBI_RATE_LIMIT": str(config.ncbi_rate_limit),
            "PIPELINE_MAX_RETRIES": str(config.max_retries),
            "PIPELINE_MAX_CONNECTION_RETRIES": str(config.max_connection_retries),
            "PIPELINE_CONNECTION_RETRY_DELAY": str(config.connection_retry_delay),
            "PIPELINE_PROMPT_VERSION": config.prompt_version,
            "PIPELINE_LLM_MAX_TOKENS": str(config.llm_max_tokens),
            "PIPELINE_MAX_RATE_LIMIT_RETRIES": str(config.max_rate_limit_retries),
            "PIPELINE_RATE_LIMIT_RETRY_DELAY": str(config.rate_limit_retry_delay),
        }
    )
    if config.progress_file:
        base["PIPELINE_PROGRESS_FILE"] = config.progress_file
    # Secrets only when present
    if secrets.ncbi_api_key:
        base["NCBI_API_KEY"] = secrets.ncbi_api_key
    if secrets.entrez_email:
        base["ENTREZ_EMAIL"] = secrets.entrez_email
    return base


# ---- High-level subprocess helper ----


async def _run_process_streamed(
    *,
    argv: list[str],
    env: dict[str, str],
    cwd: str,
    lock: SubprocessLock,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    on_stage: Callable[[str], None] | None = None,
    stage_set: list[str],
    stage_statuses: dict[str, str],
    log_lines: deque[str],
    failure_stage: str | None = None,
    on_locked: Callable[[], None] | None = None,
) -> RunResult:
    """Spawn a subprocess, stream output, parse stage markers, return RunResult.

    Merges spawn + lock + stream + stage tracking so callers (PipelineRunner,
    TuningRunner) can be mocked at a single injection point in tests.

    ``on_locked`` fires after the run-guard is acquired and before the
    subprocess is spawned, so cross-run state resets happen under the lock.
    """
    started_at = time.time()

    def _handle_stdout(line: str) -> None:
        # Cheap prefix test before invoking the regex: stage markers always
        # start with the literal "##STAGE:", and most lines do not. Avoiding
        # the regex on the hot per-line path is measurably cheaper over a
        # multi-hour, thousands-of-lines run.
        if line.startswith("##STAGE:") and (stage := parse_stage_marker(line)):
            if on_stage is not None:
                on_stage(stage)
            return
        log_lines.append(line)
        if on_stdout is not None:
            on_stdout(line)

    def _handle_stderr(line: str) -> None:
        log_lines.append(line)
        if on_stderr is not None:
            on_stderr(line)

    async with lock.run_guard():
        if on_locked is not None:
            on_locked()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
                limit=_SUBPROCESS_STREAM_LIMIT,
            )
        except OSError as spawn_exc:
            msg = f"Failed to start subprocess: {spawn_exc}"
            log_lines.append(msg)
            if on_stderr is not None:
                on_stderr(msg)
            failed = (
                failure_stage
                if failure_stage is not None
                else (stage_set[0] if stage_set else None)
            )
            if failed is not None and failed in stage_statuses:
                stage_statuses[failed] = _STATUS_FAILED
            return RunResult(exit_code=-1, report_path=None)
        lock.set_process(process)

        assert process.stdout is not None
        assert process.stderr is not None
        # Pre-bound so a non-CancelledError escape from the try below
        # leaves the post-async-with read defined.
        exit_code: int = -1
        try:
            await asyncio.gather(
                _stream_lines(process.stdout, _handle_stdout),
                _stream_lines(process.stderr, _handle_stderr),
            )
            exit_code = await process.wait()
        except asyncio.CancelledError:
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
            # Reap a still-alive process in the background so zombies don't
            # accumulate across repeated cancellations.
            if process.returncode is None:
                with contextlib.suppress(Exception):
                    asyncio.ensure_future(process.wait())
            raise

    logs_dir = Path(cwd) / "logs"
    report = find_newest_report(logs_dir, started_at)
    return RunResult(
        exit_code=exit_code,
        report_path=str(report) if report else None,
    )


# ---- PipelineRunner ----


class PipelineRunner:
    """Runs `python -m pipeline_app_hpc.cli` as a subprocess.

    Refuses to start unless `vllm_server.snapshot.state == READY`.
    """

    def __init__(self, lock: SubprocessLock, vllm_server: VllmServer) -> None:
        self._lock = lock
        self._vllm_server = vllm_server
        self.log_lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.stage_statuses: dict[str, str] = _pending_stage_statuses(PIPELINE_STAGES)
        self.last_result: RunResult | None = None
        self._current_stage: str | None = None
        self._listeners = _ListenerRegistry()

    def reset_state(self) -> None:
        # Mutates in place so a reference captured by an in-flight call to
        # `_run_process_streamed` (which holds `self.stage_statuses` as a
        # kwarg) doesn't desync from the runner's outward view.
        self.log_lines.clear()
        self.stage_statuses.clear()
        self.stage_statuses.update(_pending_stage_statuses(PIPELINE_STAGES))
        self._current_stage = None

    def add_listener(self, **kw: Any) -> Callable[[], None]:
        return self._listeners.add(PipelineListeners(**kw))

    async def run(
        self,
        config: HpcAppConfig,
        secrets: EnvSecrets,
    ) -> RunResult:
        from pipeline_app_hpc.hpc.lifecycle import VllmServerState

        # Check READY before any path validation — the test supplies an empty
        # project_root and expects RuntimeError("vLLM is not READY"), not a
        # path-validation error.
        snap = self._vllm_server.snapshot
        if snap.state != VllmServerState.READY or snap.local_url is None:
            raise RuntimeError(
                f"vLLM is not READY (state={snap.state.name}). "
                "Click Start vLLM in the HPC card first."
            )

        python = validate_python_path(config.python_path)
        project_root = validate_project_root(config.project_root)
        cli_args = build_extract_args(config)
        env = build_extract_env(config, secrets, snap.local_url)

        argv = [python, "-m", "pipeline_app_hpc.cli", *cli_args]
        try:
            result = await _run_process_streamed(
                argv=argv,
                env=env,
                cwd=project_root,
                lock=self._lock,
                on_stdout=self._emit_stdout,
                on_stderr=self._emit_stderr,
                on_stage=self._emit_stage,
                stage_set=PIPELINE_STAGES,
                stage_statuses=self.stage_statuses,
                log_lines=self.log_lines,
                failure_stage=PIPELINE_STAGES[0],
                on_locked=self.reset_state,
            )
        except asyncio.CancelledError:
            # Mark whatever was running as failed so the tracker doesn't stay
            # stuck at "running"; record a synthetic result so the page sees
            # an outcome instead of stale data from the previous run.
            if self._current_stage is not None:
                self.stage_statuses[self._current_stage] = _STATUS_FAILED
            self.last_result = RunResult(exit_code=-1, report_path=None)
            raise
        # Finalize the last running stage; without this the trailing stage
        # stays "running" forever after the marker fires for it.
        if self._current_stage is not None:
            self.stage_statuses[self._current_stage] = _final_stage_status(
                result.exit_code
            )
        elif result.exit_code != 0 and PIPELINE_STAGES:
            # Subprocess exited without ever emitting a marker (e.g. import
            # error in the CLI); flag the first stage so the user sees red.
            self.stage_statuses[PIPELINE_STAGES[0]] = _STATUS_FAILED
        self.last_result = result
        return result

    def _emit_stdout(self, line: str) -> None:
        self._listeners.emit("on_stdout", line)

    def _emit_stderr(self, line: str) -> None:
        self._listeners.emit("on_stderr", line)

    def _emit_stage(self, stage: str) -> None:
        # Markers fire when a stage *starts*. Close out the previous running
        # stage cleanly so the tracker shows a complete-then-running chain.
        if stage in self.stage_statuses:
            self._current_stage = _advance_stage(
                self.stage_statuses,
                self._current_stage,
                stage,
            )
            self._listeners.emit("on_stage", stage)


# ---- TuningRunner ----


def build_tuning_extract_config(
    main_config: HpcAppConfig,
    tuning_config: TuningConfig,
) -> HpcAppConfig:
    """Build the HpcAppConfig used for the tuning extract stage."""
    overrides: dict[str, Any] = {
        "run_mode": "local_pdfs",
        "local_pdfs_path": tuning_config.pdf_path or main_config.local_pdfs_path,
        "skip_validation": True,
        "confidence_threshold": tuning_config.confidence_threshold,
    }
    if not tuning_config.use_main_config:
        overrides.update(
            {
                "python_path": tuning_config.python_path or main_config.python_path,
                "project_root": tuning_config.project_root or main_config.project_root,
                "prompt_version": tuning_config.prompt_version,
                "vllm_base_model": tuning_config.vllm_base_model,
                "vllm_adapter_path": tuning_config.vllm_adapter_path,
                "vllm_adapter_name": tuning_config.vllm_adapter_name,
                "vllm_max_model_len": tuning_config.vllm_max_model_len,
            }
        )
    return replace(main_config, **overrides)


def get_tuning_project_root(
    main_config: HpcAppConfig,
    tuning_config: TuningConfig,
) -> str:
    """Return the project root used by tuning subprocesses."""
    if not tuning_config.use_main_config and tuning_config.project_root:
        return tuning_config.project_root
    return main_config.project_root


def get_tuning_python_path(
    main_config: HpcAppConfig,
    tuning_config: TuningConfig,
) -> str:
    """Return the Python interpreter used by tuning subprocesses."""
    if not tuning_config.use_main_config and tuning_config.python_path:
        return tuning_config.python_path
    return main_config.python_path


def _require_report_path(report_path: str | None, stage: str) -> str:
    """Return report_path or raise the stage-specific validation error."""
    if not report_path:
        raise ValueError(f"report_path is required for {stage} stage")
    return report_path


def _validate_args(tc: TuningConfig, report_path: str | None) -> list[str]:
    return [
        "scripts/validate_pipeline.py",
        _require_report_path(report_path, "validate"),
        "--reference",
        tc.gold_standard_path,
        "--local-pdfs",
    ]


def _error_analysis_args(tc: TuningConfig, report_path: str | None) -> list[str]:
    return [
        "scripts/tuning/analyze_errors.py",
        _require_report_path(report_path, "error_analysis"),
    ]


def _calibrate_args(tc: TuningConfig, report_path: str | None) -> list[str]:
    return [
        "scripts/tuning/calibrate_threshold.py",
        "--pipeline-report",
        _require_report_path(report_path, "calibrate"),
        "--reference",
        tc.gold_standard_path,
        "--local-pdfs",
        "--beta",
        str(tc.f_beta_weight),
    ]


def _track_args(tc: TuningConfig, report_path: str | None) -> list[str]:
    return [
        "scripts/tuning/track_run.py",
        "--pipeline-report",
        _require_report_path(report_path, "track"),
        "--reference",
        tc.gold_standard_path,
        "--local-pdfs",
        "--notes",
        tc.notes,
    ]


def _plot_args(_tc: TuningConfig, _report_path: str | None) -> list[str]:
    return ["scripts/plot_tuning_runs.R"]


_RSCRIPT_EXE = "Rscript"


@dataclass(slots=True, frozen=True)
class _TuningStageSpec:
    """How to invoke one tuning stage. ``extract`` is intentionally not in
    the table — it needs vLLM readiness + secrets + extract env, which
    don't fit a (TuningConfig, report_path) → argv signature."""

    executable: str  # "python" or _RSCRIPT_EXE
    build_args: Callable[[TuningConfig, str | None], list[str]]


_TUNING_STAGE_SPECS: dict[str, _TuningStageSpec] = {
    "validate": _TuningStageSpec("python", _validate_args),
    "error_analysis": _TuningStageSpec("python", _error_analysis_args),
    "calibrate": _TuningStageSpec("python", _calibrate_args),
    "track": _TuningStageSpec("python", _track_args),
    "plot": _TuningStageSpec(_RSCRIPT_EXE, _plot_args),
}


class TuningRunner:
    """Orchestrates the 6-stage tuning chain.

    Stage 1 (extract) runs `python -m pipeline_app_hpc.cli --local-pdfs <gold>`
    against vLLM. Stages 2-6 reuse `scripts/tuning/*.py` and
    `scripts/validate_pipeline.py` unchanged.
    """

    def __init__(self, lock: SubprocessLock, vllm_server: VllmServer) -> None:
        self._lock = lock
        self._vllm_server = vllm_server
        self.log_lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.stage_statuses: dict[str, str] = _pending_stage_statuses(TUNING_STAGES)
        self._current_stage: str | None = None
        self._listeners = _ListenerRegistry()

    def reset_state(self) -> None:
        """Clear log buffer and stage statuses so a fresh run starts clean."""
        # Mutate in place for stylistic consistency with PipelineRunner.
        # Unlike PipelineRunner, no on_locked hook captures this dict, so
        # rebinding would also work — kept matching to ease future merges.
        self.log_lines.clear()
        self.stage_statuses.clear()
        self.stage_statuses.update(_pending_stage_statuses(TUNING_STAGES))
        self._current_stage = None

    def add_listener(self, **kw: Any) -> Callable[[], None]:
        return self._listeners.add(PipelineListeners(**kw))

    def _emit_stdout(self, line: str) -> None:
        self._listeners.emit("on_stdout", line)

    def _emit_stderr(self, line: str) -> None:
        self._listeners.emit("on_stderr", line)

    def _emit_stage(self, stage: str) -> None:
        self._current_stage = _advance_stage(
            self.stage_statuses,
            self._current_stage,
            stage,
        )
        self._listeners.emit("on_stage", stage)

    async def cancel(self) -> None:
        await self._lock.cancel()

    @property
    def any_running(self) -> bool:
        return self._lock.is_running

    async def run_stage(
        self,
        stage: str,
        config: HpcAppConfig,
        tuning_config: TuningConfig,
        secrets: EnvSecrets,
        report_path: str | None = None,
    ) -> RunResult:
        from pipeline_app_hpc.hpc.lifecycle import VllmServerState

        project_root = validate_project_root(
            get_tuning_project_root(config, tuning_config)
        )
        python = (
            validate_python_path(get_tuning_python_path(config, tuning_config))
            if stage != "plot"
            else ""
        )
        # Reset just this stage's slot to pending so a re-run after a fail
        # lifts the red badge; preserve other stages' history.
        if stage in self.stage_statuses:
            self.stage_statuses[stage] = _STATUS_PENDING
        self._current_stage = stage

        if stage == "extract":
            snap = self._vllm_server.snapshot
            if snap.state != VllmServerState.READY or snap.local_url is None:
                raise RuntimeError(
                    "vLLM is not READY (state="
                    f"{snap.state.name}). Click Start vLLM first."
                )
            extract_cfg = build_tuning_extract_config(config, tuning_config)
            argv = [
                python,
                "-m",
                "pipeline_app_hpc.cli",
                *build_extract_args(extract_cfg),
            ]
            env = build_extract_env(extract_cfg, secrets, snap.local_url)
        else:
            spec = _TUNING_STAGE_SPECS.get(stage)
            if spec is None:
                raise ValueError(f"unknown tuning stage: {stage!r}")
            exe = python if spec.executable == "python" else _RSCRIPT_EXE
            argv = [exe, *spec.build_args(tuning_config, report_path)]
            env = _base_env()

        try:
            result = await _run_process_streamed(
                argv=argv,
                env=env,
                cwd=project_root,
                lock=self._lock,
                on_stdout=self._emit_stdout,
                on_stderr=self._emit_stderr,
                on_stage=self._emit_stage,
                stage_set=TUNING_STAGES,
                stage_statuses=self.stage_statuses,
                log_lines=self.log_lines,
                failure_stage=stage,
            )
        except asyncio.CancelledError:
            if stage in self.stage_statuses:
                self.stage_statuses[stage] = _STATUS_FAILED
            raise
        # Mirror PipelineRunner: finalize the stage we just ran. Subprocess
        # rarely emits its own ##STAGE:name## marker for tuning, so without
        # this the tracker stays "running" indefinitely.
        if stage in self.stage_statuses:
            self.stage_statuses[stage] = _final_stage_status(result.exit_code)
        return result
