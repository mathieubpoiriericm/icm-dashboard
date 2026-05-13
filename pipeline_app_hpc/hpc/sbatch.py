"""SLURM sbatch / squeue / scancel helpers, all over an SshControlMaster."""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline_app_hpc.hpc.ssh import SshControlMaster

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobInfo:
    job_id: str
    state: str
    node: str | None
    time_left_seconds: int | None
    elapsed_seconds: int | None


_SUBMITTED_RE = re.compile(r"Submitted batch job (\d+)")
_NODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_slurm_time(s: str) -> int | None:
    """Parse a SLURM time ([D-]HH:MM:SS or MM:SS or UNLIMITED) to seconds."""
    s = s.strip()
    if not s:
        return None
    if s.upper() == "UNLIMITED":
        return None
    days = 0
    if "-" in s:
        days_str, s = s.split("-", 1)
        try:
            days = int(days_str)
        except ValueError:
            return None
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:  # noqa: PLR2004
        h, m, sec = nums
    elif len(nums) == 2:  # noqa: PLR2004
        h, m, sec = 0, nums[0], nums[1]
    elif len(nums) == 1:
        h, m, sec = 0, 0, nums[0]
    else:
        return None
    return days * 86400 + h * 3600 + m * 60 + sec


def parse_squeue_line(line: str) -> JobInfo:
    """Parse one '%i|%T|%R|%L|%M' formatted squeue line into JobInfo."""
    parts = line.strip().split("|")
    if len(parts) != 5:  # noqa: PLR2004
        raise ValueError(f"unexpected squeue line: {line!r}")
    job_id, state, node_or_reason, time_left, elapsed = parts
    node = node_or_reason if _NODE_RE.match(node_or_reason) else None
    return JobInfo(
        job_id=job_id,
        state=state,
        node=node,
        time_left_seconds=_parse_slurm_time(time_left),
        elapsed_seconds=_parse_slurm_time(elapsed),
    )


async def submit_vllm_job(
    ssh: SshControlMaster,
    sbatch_remote_path: str,
    workdir: str,
    env: dict[str, str],
) -> str:
    """Submit the vLLM sbatch via `cd <workdir> && sbatch --export=ALL,...`.

    Values containing commas or newlines are rejected because SLURM splits
    ``--export`` entries on commas after shell parsing.
    """
    parts: list[str] = []
    for k, v in env.items():
        if not _ENV_NAME_RE.fullmatch(k):
            raise ValueError(f"invalid sbatch export env var name: {k!r}")
        if "," in v or "\n" in v or "\r" in v:
            raise ValueError(
                f"env value for {k!r} contains a comma or newline, which is "
                f"unsafe to forward through sbatch --export: {v!r}"
            )
        parts.append(f"{k}={shlex.quote(v)}")
    export_arg = "--export=ALL"
    if parts:
        export_arg += "," + ",".join(parts)

    workdir_quoted = shlex.quote(workdir)
    sbatch_quoted = shlex.quote(sbatch_remote_path)
    cmd = f"cd {workdir_quoted} && sbatch {export_arg} {sbatch_quoted}"

    result = await ssh.run_bash(cmd, check=True)
    m = _SUBMITTED_RE.search(result.stdout)
    if not m:
        raise RuntimeError(
            f"could not parse 'Submitted batch job N' from sbatch output: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return m.group(1)


async def get_job_info(ssh: SshControlMaster, job_id: str) -> JobInfo:
    """Fetch job state via squeue. Treat missing job as COMPLETED.

    Routes through ``run_bash`` because OpenSSH joins remote argv with
    spaces and the resulting login-shell parse turns the ``-o`` format
    string ``%i|%T|...`` into a pipeline.
    """
    job_id_q = shlex.quote(job_id)
    result = await ssh.run_bash(
        f"squeue --noheader -o '%i|%T|%R|%L|%M' --job {job_id_q}",
        check=False,
    )
    # squeue exits 1 when a terminal-state job has aged out of the queue
    # (MinJobAge). Treat as "missing" and fall back to sacct.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"squeue failed for job {job_id}: {result.stderr or result.stdout}"
        )
    if result.returncode == 0:
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            info = parse_squeue_line(line)
            if info.job_id == job_id:
                return info
    # sacct so we don't synthesize COMPLETED for jobs that actually died
    # (NODE_FAIL, OOM, etc.).
    sacct_state = await _sacct_state(ssh, job_id)
    return JobInfo(
        job_id=job_id,
        state=sacct_state or "COMPLETED",
        node=None,
        time_left_seconds=0,
        elapsed_seconds=None,
    )


async def _sacct_state(ssh: SshControlMaster, job_id: str) -> str | None:
    """Fetch the recorded state of a finished job via sacct. None on failure."""
    job_id_q = shlex.quote(job_id)
    result = await ssh.run_bash(
        f"sacct -j {job_id_q} --noheader --parsable2 -o JobID,State -X",
        check=False,
    )
    if result.returncode != 0:
        return None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:  # noqa: PLR2004
            continue
        sacct_job_id, state_field = parts
        if sacct_job_id != job_id:
            continue
        # State can be e.g. "CANCELLED by 12345" — keep only the first token.
        return state_field.split(maxsplit=1)[0] or None
    return None


async def scancel(ssh: SshControlMaster, job_id: str) -> None:
    """Cancel a SLURM job. Idempotent: scancel of a missing job is a no-op."""
    await ssh.run_bash(f"scancel {shlex.quote(job_id)}", check=False)


async def fetch_log_tail(
    ssh: SshControlMaster,
    log_path: str,
    lines: int = 100,
) -> str:
    """Return the last N lines of a remote log file. Empty string on failure."""
    result = await ssh.run_bash(
        f"tail -n {int(lines)} {shlex.quote(log_path)}",
        check=False,
    )
    return result.stdout
