"""Tests for pipeline_app_hpc.hpc.ssh."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_proc(
    returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    return proc


class TestSshResult:
    def test_fields(self):
        from pipeline_app_hpc.hpc.ssh import SshResult

        r = SshResult(returncode=0, stdout="ok", stderr="")
        assert r.returncode == 0
        assert r.stdout == "ok"
        assert r.stderr == ""


class TestSshControlMasterPaths:
    def test_default_socket_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        m = SshControlMaster(alias="my-cluster")
        assert m.socket_path == tmp_path / "my-cluster.sock"
        assert m.alias == "my-cluster"

    def test_default_socket_path_sanitizes_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        m = SshControlMaster(alias="../cluster/name")
        assert m.socket_path == tmp_path / "cluster_name.sock"

    def test_explicit_socket_path(self, tmp_path: Path):
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        sock = tmp_path / "custom.sock"
        m = SshControlMaster(alias="x", socket_path=sock)
        assert m.socket_path == sock

    def test_reconfigure_updates_alias_and_socket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        m = SshControlMaster(alias="old")
        m.reconfigure("new/cluster")
        assert m.alias == "new/cluster"
        assert m.socket_path == tmp_path / "new_cluster.sock"

        explicit = tmp_path / "explicit.sock"
        m.reconfigure("other", explicit)
        assert m.alias == "other"
        assert m.socket_path == explicit


class TestSshControlMasterLifecycle:
    @pytest.mark.asyncio
    async def test_open_runs_master_command(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        create_proc = AsyncMock(return_value=_make_proc(0))
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )

        m = SshControlMaster(alias="alpha")
        await m.open()

        args = create_proc.call_args[0]
        assert args[0] == "ssh"
        assert "-M" in args
        assert "-N" in args
        assert "-f" in args
        assert "-S" in args
        assert str(m.socket_path) in args
        assert "alpha" in args

    @pytest.mark.asyncio
    async def test_open_clears_stale_socket(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        m = SshControlMaster(alias="beta")
        m.socket_path.parent.mkdir(parents=True, exist_ok=True)
        m.socket_path.touch()

        check_proc = _make_proc(1)
        master_proc = _make_proc(0)
        create_proc = AsyncMock(side_effect=[check_proc, master_proc])
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )

        await m.open()
        assert create_proc.call_count == 2

    @pytest.mark.asyncio
    async def test_is_alive_runs_check(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        create_proc = AsyncMock(return_value=_make_proc(0))
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )

        m = SshControlMaster(alias="g")
        assert (await m.is_alive()) is True
        args = create_proc.call_args[0]
        assert "-O" in args and "check" in args

    @pytest.mark.asyncio
    async def test_close_idempotent(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        create_proc = AsyncMock(return_value=_make_proc(0))
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )
        m = SshControlMaster(alias="h")
        await m.close()
        await m.close()  # second call must not raise


class TestSshControlMasterRun:
    @pytest.mark.asyncio
    async def test_run_returns_result(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        proc = _make_proc(0, stdout=b"output\n", stderr=b"warn\n")
        create_proc = AsyncMock(return_value=proc)
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )

        m = SshControlMaster(alias="x")
        r = await m.run(["echo", "hi"])
        assert r.returncode == 0
        assert r.stdout == "output\n"
        assert r.stderr == "warn\n"

        args = create_proc.call_args[0]
        assert args[0] == "ssh"
        assert "-S" in args
        assert "x" in args
        assert "echo" in args and "hi" in args

    @pytest.mark.asyncio
    async def test_run_raises_on_nonzero_when_check_true(
        self, tmp_path, monkeypatch, mocker
    ):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster, SshError

        proc = _make_proc(2, stderr=b"boom")
        create_proc = AsyncMock(return_value=proc)
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )
        m = SshControlMaster(alias="x")
        with pytest.raises(SshError, match="boom"):
            await m.run(["false"], check=True)

    @pytest.mark.asyncio
    async def test_run_returns_on_nonzero_when_check_false(
        self, tmp_path, monkeypatch, mocker
    ):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        proc = _make_proc(1, stderr=b"err")
        create_proc = AsyncMock(return_value=proc)
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )
        m = SshControlMaster(alias="x")
        r = await m.run(["false"], check=False)
        assert r.returncode == 1

    @pytest.mark.asyncio
    async def test_run_bash_sends_one_quoted_remote_command(
        self, tmp_path, monkeypatch, mocker
    ):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        proc = _make_proc(0)
        create_proc = AsyncMock(return_value=proc)
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )
        m = SshControlMaster(alias="x")
        await m.run_bash("mkdir -p '/tmp/work' && echo ready")

        args = create_proc.call_args[0]
        assert args[-1].startswith("bash -lc ")
        assert "mkdir -p" in args[-1]


class TestSshControlMasterForward:
    @pytest.mark.asyncio
    async def test_add_forward(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        proc = _make_proc(0)
        create_proc = AsyncMock(return_value=proc)
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )

        m = SshControlMaster(alias="x")
        await m.add_forward(
            local_port=30800, remote_host="sphpc-gpu05", remote_port=8000
        )

        args = create_proc.call_args[0]
        assert "-O" in args and "forward" in args
        assert "-L" in args
        assert "30800:sphpc-gpu05:8000" in args

    @pytest.mark.asyncio
    async def test_remove_forward(self, tmp_path, monkeypatch, mocker):
        from pipeline_app_hpc.hpc import ssh as ssh_mod

        monkeypatch.setattr(ssh_mod, "SOCKET_DIR", tmp_path)
        from pipeline_app_hpc.hpc.ssh import SshControlMaster

        proc = _make_proc(0)
        create_proc = AsyncMock(return_value=proc)
        mocker.patch(
            "pipeline_app_hpc.hpc.ssh.asyncio.create_subprocess_exec", create_proc
        )
        m = SshControlMaster(alias="x")
        await m.remove_forward(
            local_port=30800, remote_host="sphpc-gpu05", remote_port=8000
        )

        args = create_proc.call_args[0]
        assert "-O" in args and "cancel" in args
        assert "-L" in args
        # OpenSSH matches the full triple on cancel — the spec must mirror
        # what add_forward registered, not a placeholder.
        assert "30800:sphpc-gpu05:8000" in args
