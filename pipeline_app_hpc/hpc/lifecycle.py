"""vLLM server lifecycle: state machine over sbatch + tunnel + readiness."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import shlex
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pipeline_app_hpc.hpc.readiness import wait_until_ready
from pipeline_app_hpc.hpc.sbatch import (
    fetch_log_tail,
    get_job_info,
    scancel,
    submit_vllm_job,
)
from pipeline_app_hpc.hpc.tunnel import VllmTunnel

if TYPE_CHECKING:
    from pipeline_app_hpc.config import HpcAppConfig
    from pipeline_app_hpc.hpc.ssh import SshControlMaster

logger = logging.getLogger(__name__)

_PORT_MARKER_RE = re.compile(r"##VLLM_PORT=(\d+)##")
_SAFE_SBATCH_VALUE_RE = re.compile(r"^[A-Za-z0-9._/@:+,=-]+$")
_SLURM_FAILED_STATES = frozenset(
    {
        "BOOT_FAIL",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
        "SPECIAL_EXIT",
        # SUSPENDED + STOPPED: an admin paused the job (e.g. for cluster
        # maintenance). The vLLM process is paused and HTTP requests through
        # the tunnel will hang. Treating these as failure surfaces the
        # problem so the user can take action.
        "STOPPED",
        "SUSPENDED",
        "TIMEOUT",
    }
)

# ---- Module-level constants ----

POLL_INTERVAL_SECONDS: float = 5.0
LOG_TAIL_REFRESH_INTERVAL: float = 30.0
LOG_TAIL_LINES: int = 100

_TEMPLATE_PATH = Path(__file__).parent.parent / "sbatch" / "vllm_serve.sbatch.j2"


@functools.cache
def _template_text() -> str:
    """Read the sbatch template once. Lazy so a missing file surfaces only
    when a job is actually submitted, not on every import."""
    return _TEMPLATE_PATH.read_text()


# ---- State enum ----


class VllmServerState(Enum):
    IDLE = "idle"
    SUBMITTED = "submitted"
    ALLOCATED = "allocated"
    READY = "ready"
    DRAINING = "draining"
    FAILED = "failed"


_ACTIVE_STATES = frozenset(
    {
        VllmServerState.SUBMITTED,
        VllmServerState.ALLOCATED,
        VllmServerState.READY,
        VllmServerState.DRAINING,
    }
)


# ---- Snapshot dataclass ----


@dataclass(frozen=True, slots=True)
class VllmServerSnapshot:
    state: VllmServerState
    job_id: str | None
    node: str | None
    local_url: str | None
    time_left_seconds: int | None
    error: str | None
    last_log_tail: str

    @property
    def is_ready(self) -> bool:
        return self.state == VllmServerState.READY


# ---- Template helpers ----


async def rsync_sbatch_template(
    ssh: SshControlMaster,
    workdir: str,
    rendered_text: str,
) -> str:
    """Write the rendered sbatch text to <workdir>/vllm_serve.sbatch on HPC."""
    remote_path = f"{workdir}/vllm_serve.sbatch"
    marker = "CSVD_HPC_EOF"
    while marker in rendered_text:
        marker = f"CSVD_HPC_EOF_{uuid.uuid4().hex}"
    # Use a heredoc via bash -c so we don't depend on local rsync — works
    # over the existing SSH master. Single-quoted EOF marker prevents variable
    # expansion in the template body.
    # mkdir + heredoc + chmod in one round-trip; the heredoc body alone can
    # exceed the 30s default over the control-master on slow NFS.
    # `set -e` is essential: without it, a `cat` that fails partway (e.g. NFS
    # quota exceeded) leaves a truncated file and the next `chmod +x` runs
    # unconditionally on a separate logical line, masking the failure with a
    # zero exit code and silently submitting a corrupted sbatch script.
    cmd = (
        f"set -e\n"
        f"mkdir -p {shlex.quote(workdir)}\n"
        f"cat > {shlex.quote(remote_path)} << '{marker}'\n"
        f"{rendered_text}\n{marker}\n"
        f"chmod +x {shlex.quote(remote_path)}"
    )
    await ssh.run_bash(cmd, check=True, timeout=120.0)
    return remote_path


def _render_template(config: HpcAppConfig) -> str:
    """Render the sbatch template with per-config substitutions."""
    _validate_template_config(config)
    text = _template_text()
    # Minimal substitution — no Jinja2 dep needed for these straight replacements.
    for placeholder, value in (
        ("account", config.vllm_account),
        ("partition", config.vllm_partition),
        ("qos", config.vllm_qos),
        ("time_limit", config.vllm_time_limit),
        ("cpus_per_task", config.vllm_cpus_per_task),
        ("mem", config.vllm_mem),
        ("log_dir", config.vllm_remote_log_dir),
        ("venv_path", config.vllm_remote_venv_path),
        ("hf_home", config.vllm_hf_home),
    ):
        text = text.replace(f"{{{{ {placeholder} }}}}", str(value))
    return text


def _require_sbatch_safe_value(name: str, value: object) -> str:
    """Return a string that is safe to splice into the sbatch script template."""
    value_s = str(value)
    if not value_s:
        raise ValueError(f"{name} must not be empty")
    if not _SAFE_SBATCH_VALUE_RE.fullmatch(value_s):
        raise ValueError(
            f"{name} contains characters that are unsafe in the sbatch template: "
            f"{value_s!r}"
        )
    return value_s


def _require_remote_path(name: str, value: str) -> str:
    """Validate remote paths rendered into #SBATCH and shell lines."""
    value_s = _require_sbatch_safe_value(name, value)
    if not value_s.startswith("/"):
        raise ValueError(f"{name} must be an absolute remote path: {value_s!r}")
    return value_s.rstrip("/") or "/"


def _validate_template_config(config: HpcAppConfig) -> None:
    """Reject values that could inject directives or shell into the template."""
    for name in (
        "vllm_account",
        "vllm_partition",
        "vllm_qos",
        "vllm_time_limit",
        "vllm_mem",
        "vllm_cpus_per_task",
    ):
        _require_sbatch_safe_value(name, getattr(config, name))
    for name in (
        "vllm_remote_workdir",
        "vllm_remote_log_dir",
        "vllm_remote_venv_path",
        "vllm_hf_home",
    ):
        _require_remote_path(name, getattr(config, name))


def _build_sbatch_env(config: HpcAppConfig) -> dict[str, str]:
    """Build the env dict forwarded to sbatch via --export."""
    env: dict[str, str] = {
        "VLLM_BASE_MODEL": config.vllm_base_model,
        "VLLM_MAX_MODEL_LEN": str(config.vllm_max_model_len),
        # Forward quantization to the sbatch script — the template builds
        # --quantization / --load-format from this so changing the dropdown
        # in the GUI actually affects the served model.
        "VLLM_QUANTIZATION": config.vllm_quantization,
    }
    if config.vllm_adapter_path:
        env["VLLM_ADAPTER_PATH"] = config.vllm_adapter_path
        env["VLLM_ADAPTER_NAME"] = config.vllm_adapter_name
        env["VLLM_MAX_LORA_RANK"] = str(config.vllm_max_lora_rank)
    return env


# ---- VllmServer ----


class VllmServer:
    """Single owner of vLLM-on-HPC state. The GUI binds to this."""

    def __init__(
        self,
        ssh: SshControlMaster,
        config: HpcAppConfig,
    ) -> None:
        self._ssh = ssh
        self._config = config
        self._snapshot = VllmServerSnapshot(
            state=VllmServerState.IDLE,
            job_id=None,
            node=None,
            local_url=None,
            time_left_seconds=None,
            error=None,
            last_log_tail="",
        )
        self._subscribers: list[Callable[[VllmServerSnapshot], None]] = []
        self._lock = asyncio.Lock()
        self._poller_task: asyncio.Task[None] | None = None
        self._probe_task: asyncio.Task[None] | None = None
        self._tunnel: VllmTunnel | None = None
        self._last_log_fetch: float = 0.0

    @property
    def snapshot(self) -> VllmServerSnapshot:
        return self._snapshot

    def update_config(self, config: HpcAppConfig) -> bool:
        """Replace future-start settings while no vLLM job is active."""
        if self._snapshot.state not in (VllmServerState.IDLE, VllmServerState.FAILED):
            return False
        socket_path = Path(config.ssh_socket_path) if config.ssh_socket_path else None
        self._ssh.reconfigure(config.ssh_alias, socket_path)
        self._config = replace(config)
        return True

    def subscribe(
        self, callback: Callable[[VllmServerSnapshot], None]
    ) -> Callable[[], None]:
        """Register a state-change listener; returns a dispose function."""
        self._subscribers.append(callback)

        def _dispose() -> None:
            with suppress(ValueError):
                self._subscribers.remove(callback)

        return _dispose

    def _publish(self) -> None:
        """Notify all subscribers of the current snapshot."""
        for cb in list(self._subscribers):
            try:
                cb(self._snapshot)
            except Exception:
                logger.exception("vllm-server subscriber raised")

    def _set(self, **kw: object) -> None:
        """Replace the snapshot with selected field overrides, then publish."""
        new_snapshot = replace(self._snapshot, **kw)  # type: ignore[arg-type]
        if new_snapshot == self._snapshot:
            return  # no-op; skip publish
        self._snapshot = new_snapshot
        self._publish()

    async def _close_tunnel(self) -> None:
        """Close and clear the local SSH tunnel if one is open."""
        if self._tunnel is None:
            return
        try:
            await self._tunnel.close()
        except Exception:
            logger.exception("tunnel.close raised during teardown")
        finally:
            self._tunnel = None

    async def start(self) -> None:
        """Open SSH, submit sbatch job, transition to SUBMITTED, start poller."""
        async with self._lock:
            # DRAINING means a previous stop() is still tearing down; accepting
            # a new start now would race two tunnels onto the same local port.
            if self._snapshot.state in _ACTIVE_STATES:
                return  # idempotent
            # If a previous run failed and left a poller alive, drain it
            # before spawning a new one — otherwise we leak a background task
            # on every retry-after-failure.
            await self._cancel_task(self._poller_task)
            self._poller_task = None
            await self._cancel_task(self._probe_task)
            self._probe_task = None
            await self._close_tunnel()
            self._set(
                job_id=None,
                node=None,
                local_url=None,
                time_left_seconds=None,
                last_log_tail="",
            )
            job_id: str | None = None
            try:
                rendered = _render_template(self._config)
                env = _build_sbatch_env(self._config)
                await self._ssh.open()
                # SLURM opens --output/--error before the script body runs;
                # the log dir must exist at submission time. Run in parallel
                # with the sbatch heredoc write — disjoint paths, multiplexed
                # by the SSH control master.
                log_dir_q = shlex.quote(self._config.vllm_remote_log_dir)
                _, remote_sbatch = await asyncio.gather(
                    self._ssh.run_bash(
                        f"mkdir -p {log_dir_q}", check=True
                    ),
                    rsync_sbatch_template(
                        self._ssh, self._config.vllm_remote_workdir, rendered
                    ),
                )
                job_id = await submit_vllm_job(
                    self._ssh,
                    sbatch_remote_path=remote_sbatch,
                    workdir=self._config.vllm_remote_workdir,
                    env=env,
                )
                self._set(
                    state=VllmServerState.SUBMITTED, job_id=job_id, error=None
                )
                self._poller_task = asyncio.create_task(self._run_poller())
            except BaseException as e:
                # BaseException catches CancelledError too; without it a
                # cancel between submit_vllm_job and _set leaks the job_id
                # and orphans the SLURM job until time-limit.
                if job_id is not None:
                    with suppress(Exception):
                        await scancel(self._ssh, job_id)
                err = str(e) or e.__class__.__name__
                self._set(
                    state=VllmServerState.FAILED, job_id=None, error=err
                )
                raise

    async def stop(self) -> None:
        """Cancel the SLURM job and tear down all local resources synchronously.

        Closes the tunnel and cancels both poller and probe tasks even if the
        poller has already exited (e.g. after FAILED). Without this, a tunnel
        opened during ALLOCATED would leak forever once the state machine
        transitioned to FAILED via readiness timeout.
        """
        async with self._lock:
            if self._snapshot.state == VllmServerState.IDLE:
                return
            # Mark transitional state up front so a concurrent start() bails.
            self._set(state=VllmServerState.DRAINING)
            # Probe and poller hold no shared state — cancel both at once
            # to halve the SIGTERM round-trip wait before we send scancel.
            await asyncio.gather(
                self._cancel_task(self._probe_task),
                self._cancel_task(self._poller_task),
            )
            self._probe_task = None
            self._poller_task = None
            job_id = self._snapshot.job_id
            # scancel hits SLURM; _close_tunnel hits the SSH master to remove
            # a port forward. Independent — gather so the user-visible stop
            # latency is one SSH RTT instead of two.
            scancel_error: str | None = None
            if job_id is not None:
                results = await asyncio.gather(
                    scancel(self._ssh, job_id),
                    self._close_tunnel(),
                    return_exceptions=True,
                )
                cancel_exc = results[0]
                if isinstance(cancel_exc, BaseException):
                    logger.exception(
                        "scancel failed for job %s",
                        job_id,
                        exc_info=cancel_exc,
                    )
                    scancel_error = (
                        f"scancel failed — job {job_id} may still be running "
                        "on HPC; verify with 'squeue --me' before retrying."
                    )
                tunnel_exc = results[1]
                if isinstance(tunnel_exc, BaseException):
                    logger.exception(
                        "tunnel close raised during stop()",
                        exc_info=tunnel_exc,
                    )
            else:
                await self._close_tunnel()
            # ssh.close must come last — _close_tunnel uses the master.
            with suppress(Exception):
                await self._ssh.close()
            if scancel_error is not None:
                self._set(
                    state=VllmServerState.FAILED,
                    job_id=None,
                    node=None,
                    local_url=None,
                    time_left_seconds=None,
                    error=scancel_error,
                )
                return
            self._set(
                state=VllmServerState.IDLE,
                job_id=None,
                node=None,
                local_url=None,
                time_left_seconds=None,
                error=None,
            )

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None] | None) -> None:
        """Cancel a task and await its termination, swallowing CancelledError."""
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    async def _run_poller(self) -> None:
        """Poll squeue at POLL_INTERVAL_SECONDS until terminal state."""
        try:
            while self._snapshot.state not in (
                VllmServerState.IDLE,
                VllmServerState.FAILED,
            ):
                try:
                    await self._poll_once()
                except Exception:
                    logger.exception("vllm-server poll iteration raised")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            self._poller_task = None

    async def _poll_once(self) -> None:
        """One poll iteration: refresh state from squeue + log tail."""
        job_id = self._snapshot.job_id
        if job_id is None:
            return

        log_path = f"{self._config.vllm_remote_log_dir}/svd-vllm-{job_id}.err"
        now = time.monotonic()
        # `prefetched_tail` is set whenever this iteration already paid for
        # a fetch_log_tail call concurrently with get_job_info, so the
        # downstream FAILED / log-refresh branches can reuse it instead of
        # paying a second SSH RTT.
        prefetched_tail: str | None = None

        # SUBMITTED → ALLOCATED: job is RUNNING, has a node, and port marker present.
        if self._snapshot.state == VllmServerState.SUBMITTED:
            info, prefetched_tail = await asyncio.gather(
                get_job_info(self._ssh, job_id),
                fetch_log_tail(self._ssh, log_path, lines=LOG_TAIL_LINES),
            )
            if info.state == "RUNNING":
                m = _PORT_MARKER_RE.search(prefetched_tail)
                if info.node and m:
                    remote_port = int(m.group(1))
                    self._tunnel = VllmTunnel(
                        self._ssh,
                        local_port=self._config.vllm_local_port,
                        remote_port=remote_port,
                    )
                    try:
                        await self._tunnel.open(remote_host=info.node)
                    except Exception as e:
                        self._set(
                            state=VllmServerState.FAILED,
                            error=f"tunnel open failed: {e}",
                            last_log_tail=prefetched_tail,
                        )
                        return
                    self._set(
                        state=VllmServerState.ALLOCATED,
                        node=info.node,
                        local_url=self._tunnel.local_url,
                        time_left_seconds=info.time_left_seconds,
                        last_log_tail=prefetched_tail,
                    )
                    # Kick off readiness probe in background
                    self._probe_task = asyncio.create_task(self._probe_ready())
                    return
        elif (
            self._snapshot.state
            in (VllmServerState.ALLOCATED, VllmServerState.READY)
            and now - self._last_log_fetch >= LOG_TAIL_REFRESH_INTERVAL
        ):
            # The log-tail refresh window is the dominant case here: rather
            # than serializing squeue → conditional fetch_log_tail (two SSH
            # RTTs), gather both up front. Worst case the tail is unused.
            info, prefetched_tail = await asyncio.gather(
                get_job_info(self._ssh, job_id),
                fetch_log_tail(self._ssh, log_path, lines=LOG_TAIL_LINES),
            )
        else:
            info = await get_job_info(self._ssh, job_id)

        # A user-initiated stop lands here as DRAINING/COMPLETED or
        # CANCELLED. Any other COMPLETED means the long-running vLLM service
        # exited before the app asked it to stop, so surface it as failure.
        if (
            info.state == "COMPLETED"
            and self._snapshot.state != VllmServerState.DRAINING
        ):
            tail = prefetched_tail or await fetch_log_tail(
                self._ssh, log_path, lines=LOG_TAIL_LINES
            )
            await self._close_tunnel()
            self._set(
                state=VllmServerState.FAILED,
                error="SLURM job completed before vLLM was stopped",
                last_log_tail=tail,
                local_url=None,
                time_left_seconds=None,
            )
            return

        if info.state in ("COMPLETED", "CANCELLED"):
            await self._close_tunnel()
            self._set(
                state=VllmServerState.IDLE,
                job_id=None,
                node=None,
                local_url=None,
                time_left_seconds=None,
            )
            return

        # SLURM failure states → fetch log tail, transition to FAILED.
        # squeue can report several terminal failures beyond plain FAILED;
        # leaving those unhandled makes the UI appear stuck forever.
        if info.state in _SLURM_FAILED_STATES:
            tail = prefetched_tail or await fetch_log_tail(
                self._ssh, log_path, lines=LOG_TAIL_LINES
            )
            await self._close_tunnel()
            self._set(
                state=VllmServerState.FAILED,
                error=f"SLURM job entered {info.state} state",
                last_log_tail=tail,
                local_url=None,
                time_left_seconds=None,
            )
            return

        # RUNNING in ALLOCATED/READY: refresh time-left and (throttled) log tail.
        if self._snapshot.state in (VllmServerState.ALLOCATED, VllmServerState.READY):
            if prefetched_tail is not None:
                self._last_log_fetch = now
                self._set(
                    time_left_seconds=info.time_left_seconds,
                    last_log_tail=prefetched_tail,
                )
            else:
                self._set(time_left_seconds=info.time_left_seconds)

    async def _probe_ready(self) -> None:
        """Wait for vLLM /v1/models 200 and transition ALLOCATED → READY."""
        url = self._snapshot.local_url
        if url is None:
            return
        try:
            await wait_until_ready(url, timeout=self._config.vllm_readiness_timeout)
        except asyncio.CancelledError:
            return  # stop() cancelled us; that's expected
        except TimeoutError as e:
            # Auto-cancel the orphan job on readiness timeout
            jid = self._snapshot.job_id
            if jid is not None:
                try:
                    await scancel(self._ssh, jid)
                except Exception:
                    logger.exception("scancel during readiness-timeout failed")
            await self._close_tunnel()
            self._set(
                state=VllmServerState.FAILED,
                error=str(e),
                local_url=None,
                time_left_seconds=None,
            )
            return
        except Exception as e:
            await self._close_tunnel()
            self._set(
                state=VllmServerState.FAILED,
                error=f"readiness probe failed: {e}",
                local_url=None,
                time_left_seconds=None,
            )
            return
        # Race-guard: only flip to READY if we're still in ALLOCATED
        if self._snapshot.state == VllmServerState.ALLOCATED:
            self._set(state=VllmServerState.READY)
