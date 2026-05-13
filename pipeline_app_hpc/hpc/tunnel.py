"""SSH -L tunnel for vLLM HTTP traffic."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline_app_hpc.hpc.ssh import SshControlMaster


class VllmTunnel:
    """Manages a single -L port forward via the SSH control master."""

    def __init__(
        self,
        ssh: SshControlMaster,
        local_port: int,
        remote_port: int,
    ) -> None:
        self._ssh = ssh
        self._local_port = local_port
        self._remote_port = remote_port
        # Stored on open() so close() can reproduce the exact spec OpenSSH
        # used to register the forward — mismatched specs are silently
        # rejected by `ssh -O cancel`.
        self._remote_host: str | None = None
        self._open = False

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self._local_port}"

    async def open(self, remote_host: str) -> None:
        """Add the -L forward via the master. Raises on local-port conflict."""
        await self._ssh.add_forward(
            local_port=self._local_port,
            remote_host=remote_host,
            remote_port=self._remote_port,
        )
        self._remote_host = remote_host
        self._open = True

    async def close(self) -> None:
        """Cancel the -L forward. Idempotent."""
        if not self._open or self._remote_host is None:
            return
        await self._ssh.remove_forward(
            local_port=self._local_port,
            remote_host=self._remote_host,
            remote_port=self._remote_port,
        )
        self._remote_host = None
        self._open = False

    async def reopen(self, new_remote_host: str) -> None:
        """Close + open against a different remote host."""
        await self.close()
        await self.open(new_remote_host)
