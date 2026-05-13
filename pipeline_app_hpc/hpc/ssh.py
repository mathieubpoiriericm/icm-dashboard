"""SSH ControlMaster: persistent multiplexed SSH connection to the HPC."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SOCKET_DIR: Path = Path.home() / ".cache" / "csvd-hpc"
SSH_MASTER_OPEN_TIMEOUT_SECONDS: float = 120.0
SSH_CONTROL_COMMAND_TIMEOUT_SECONDS: float = 30.0

_SAFE_SOCKET_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _socket_name_for_alias(alias: str) -> str:
    """Return a filesystem-safe socket filename for an SSH alias."""
    safe = _SAFE_SOCKET_CHARS_RE.sub("_", alias).strip("._") or "ssh"
    return f"{safe}.sock"


@dataclass(frozen=True, slots=True)
class SshResult:
    returncode: int
    stdout: str
    stderr: str


class SshError(RuntimeError):
    """Raised when an ssh subprocess exits non-zero and `check=True`."""


class SshControlMaster:
    """Owns one persistent ssh master connection, reusable for many ops."""

    def __init__(self, alias: str, socket_path: Path | None = None) -> None:
        self._alias = alias
        if socket_path is None:
            socket_path = SOCKET_DIR / _socket_name_for_alias(alias)
        self._socket_path = socket_path

    @property
    def alias(self) -> str:
        return self._alias

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def reconfigure(self, alias: str, socket_path: Path | None = None) -> None:
        """Update future SSH commands to use a new alias/socket path.

        Callers must only use this while no master connection or tunnel is
        active. The lifecycle owner enforces that by reconfiguring only from
        IDLE/FAILED states.
        """
        self._alias = alias
        self._socket_path = (
            socket_path
            if socket_path is not None
            else SOCKET_DIR / _socket_name_for_alias(alias)
        )

    async def is_alive(self) -> bool:
        """Run `ssh -S <sock> -O check` against the master."""
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-S",
            str(self._socket_path),
            "-O",
            "check",
            self._alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(
                proc.communicate(),
                timeout=SSH_CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False
        return proc.returncode == 0

    async def open(self) -> None:
        """Open the master, clearing any stale socket file. Idempotent."""
        if self._socket_path.exists():
            if await self.is_alive():
                return  # already alive
            with contextlib.suppress(OSError):
                self._socket_path.unlink()

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-M",
            "-N",
            "-f",
            "-S",
            str(self._socket_path),
            "-o",
            "ControlPersist=10m",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            self._alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=SSH_MASTER_OPEN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SshError(
                "ssh master open timed out after "
                f"{SSH_MASTER_OPEN_TIMEOUT_SECONDS:.0f}s for alias "
                f"{self._alias!r}. Check whether SSH is still waiting for "
                "a password, MFA prompt, host-key confirmation, or network access."
            ) from None
        if proc.returncode != 0:
            raise SshError(
                f"ssh master open failed for alias {self._alias!r}: "
                f"{stderr.decode(errors='replace')}"
            )

    async def close(self) -> None:
        """Send `-O exit` to the master. Idempotent: silent on already-closed.

        ``ssh -O exit`` on a missing socket simply prints ``No such file`` to
        stderr and exits non-zero — we don't surface it, so the pre-check
        ``exists()`` syscall would only save a subprocess spawn during
        idempotent shutdowns. Skipping it removes a TOCTOU window between
        the check and the exec.
        """
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-S",
            str(self._socket_path),
            "-O",
            "exit",
            self._alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(
                proc.communicate(),
                timeout=SSH_CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def run(
        self,
        argv: list[str],
        timeout: float = 30.0,
        check: bool = True,
    ) -> SshResult:
        """Run a remote command via the control master."""
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-S",
            str(self._socket_path),
            self._alias,
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SshError(f"ssh command timed out after {timeout}s: {argv}") from None

        result = SshResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
        if check and result.returncode != 0:
            raise SshError(
                f"ssh command failed (rc={result.returncode}): {result.stderr}"
            )
        return result

    async def run_bash(
        self,
        command: str,
        timeout: float = 30.0,
        check: bool = True,
    ) -> SshResult:
        """Run a shell script through SSH as one quoted remote command."""
        return await self.run(
            [f"bash -lc {shlex.quote(command)}"],
            timeout=timeout,
            check=check,
        )

    async def add_forward(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
    ) -> None:
        """Add a -L port forward via the master."""
        spec = f"{local_port}:{remote_host}:{remote_port}"
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-S",
            str(self._socket_path),
            "-O",
            "forward",
            "-L",
            spec,
            self._alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=SSH_CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SshError(
                f"ssh -O forward -L {spec} timed out after "
                f"{SSH_CONTROL_COMMAND_TIMEOUT_SECONDS:.0f}s"
            ) from None
        if proc.returncode != 0:
            raise SshError(
                f"ssh -O forward -L {spec} failed: {stderr.decode(errors='replace')}"
            )

    async def remove_forward(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
    ) -> None:
        """Cancel a -L port forward via the master. Tolerates 'no such forward'.

        OpenSSH matches the full ``[bind:]port:host:hostport`` string when
        cancelling — destination fields are NOT ignored. Callers must pass
        the same triple they used for ``add_forward`` or the cancel will
        silently fail and leave the forward (and the local port) in use.
        """
        spec = f"{local_port}:{remote_host}:{remote_port}"
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-S",
            str(self._socket_path),
            "-O",
            "cancel",
            "-L",
            spec,
            self._alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(
                proc.communicate(),
                timeout=SSH_CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
