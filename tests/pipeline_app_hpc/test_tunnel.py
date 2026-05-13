"""Tests for pipeline_app_hpc.hpc.tunnel."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestVllmTunnel:
    def test_local_url(self):
        from pipeline_app_hpc.hpc.tunnel import VllmTunnel

        ssh = MagicMock()
        t = VllmTunnel(ssh, local_port=30800, remote_port=8000)
        assert t.local_url == "http://127.0.0.1:30800"

    @pytest.mark.asyncio
    async def test_open_calls_add_forward(self):
        from pipeline_app_hpc.hpc.tunnel import VllmTunnel

        ssh = MagicMock()
        ssh.add_forward = AsyncMock()
        t = VllmTunnel(ssh, local_port=30800, remote_port=8000)
        await t.open(remote_host="sphpc-gpu05")
        ssh.add_forward.assert_awaited_once_with(
            local_port=30800, remote_host="sphpc-gpu05", remote_port=8000
        )

    @pytest.mark.asyncio
    async def test_close_calls_remove_forward(self):
        from pipeline_app_hpc.hpc.tunnel import VllmTunnel

        ssh = MagicMock()
        ssh.add_forward = AsyncMock()
        ssh.remove_forward = AsyncMock()
        t = VllmTunnel(ssh, local_port=30800, remote_port=8000)
        await t.open(remote_host="sphpc-gpu05")
        await t.close()
        # close() must reproduce the exact triple used by add_forward, or
        # OpenSSH silently ignores the cancel and the local port leaks.
        ssh.remove_forward.assert_awaited_once_with(
            local_port=30800, remote_host="sphpc-gpu05", remote_port=8000
        )

    @pytest.mark.asyncio
    async def test_close_idempotent_when_never_opened(self):
        from pipeline_app_hpc.hpc.tunnel import VllmTunnel

        ssh = MagicMock()
        ssh.remove_forward = AsyncMock()
        t = VllmTunnel(ssh, local_port=30800, remote_port=8000)
        await t.close()  # never opened — must not raise
        ssh.remove_forward.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reopen_closes_then_opens(self):
        from pipeline_app_hpc.hpc.tunnel import VllmTunnel

        ssh = MagicMock()
        ssh.add_forward = AsyncMock()
        ssh.remove_forward = AsyncMock()
        t = VllmTunnel(ssh, local_port=30800, remote_port=8000)
        await t.open(remote_host="sphpc-gpu05")
        await t.reopen("sphpc-gpu06")
        # remove_forward must use the original host the forward was opened
        # against, not the new one.
        ssh.remove_forward.assert_awaited_once_with(
            local_port=30800, remote_host="sphpc-gpu05", remote_port=8000
        )
        # add_forward called twice: original open, then reopen
        assert ssh.add_forward.await_count == 2
        last = ssh.add_forward.call_args_list[-1]
        assert last.kwargs["remote_host"] == "sphpc-gpu06"
