"""Tests for pipeline_app_hpc.hpc.lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestVllmServerState:
    def test_states_present(self):
        from pipeline_app_hpc.hpc.lifecycle import VllmServerState

        for n in ("IDLE", "SUBMITTED", "ALLOCATED", "READY", "DRAINING", "FAILED"):
            assert hasattr(VllmServerState, n)


class TestVllmServerSnapshot:
    def test_default_snapshot(self):
        from pipeline_app_hpc.hpc.lifecycle import (
            VllmServerSnapshot,
            VllmServerState,
        )

        s = VllmServerSnapshot(
            state=VllmServerState.IDLE,
            job_id=None,
            node=None,
            local_url=None,
            time_left_seconds=None,
            error=None,
            last_log_tail="",
        )
        assert s.state == VllmServerState.IDLE
        assert s.job_id is None


def _make_config(**overrides):
    from pipeline_app_hpc.config import HpcAppConfig

    return HpcAppConfig(**overrides)


class TestVllmServerSubscribe:
    def test_initial_snapshot_is_idle(self):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        assert srv.snapshot.state == VllmServerState.IDLE
        assert srv.snapshot.job_id is None

    def test_subscribe_and_dispose(self):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        called: list[object] = []
        dispose = srv.subscribe(lambda snap: called.append(snap))
        srv._publish()  # internal helper drives subscribers
        assert len(called) == 1
        dispose()
        srv._publish()
        assert len(called) == 1  # no further notifications

    def test_update_config_only_when_inactive(self):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        ssh.reconfigure = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config(vllm_local_port=30800))

        assert (
            srv.update_config(
                _make_config(
                    vllm_local_port=31000,
                    ssh_alias="new-alias",
                    ssh_socket_path="/tmp/new.sock",
                )
            )
            is True
        )
        assert srv._config.vllm_local_port == 31000
        ssh.reconfigure.assert_called_once()
        assert ssh.reconfigure.call_args.args[0] == "new-alias"
        assert str(ssh.reconfigure.call_args.args[1]) == "/tmp/new.sock"

        srv._set(state=VllmServerState.SUBMITTED)
        assert srv.update_config(_make_config(vllm_local_port=32000)) is False
        assert srv._config.vllm_local_port == 31000


class TestVllmServerStart:
    @pytest.mark.asyncio
    async def test_start_submits_and_transitions_to_submitted(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        ssh.open = AsyncMock()
        ssh.is_alive = AsyncMock(return_value=True)
        ssh.run = AsyncMock()  # used by sbatch.submit_vllm_job
        ssh.run_bash = AsyncMock()  # mkdir -p log_dir before submit
        srv = VllmServer(ssh=ssh, config=_make_config())

        # Patch sbatch helpers so we don't shell out
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle._render_template",
            return_value="dummy_sbatch",
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.rsync_sbatch_template",
            new=AsyncMock(),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.submit_vllm_job",
            new=AsyncMock(return_value="2398127"),
        )
        # Make the poller a no-op so we can inspect the SUBMITTED state
        mocker.patch.object(srv, "_run_poller", new=AsyncMock())

        await srv.start()
        assert srv.snapshot.state == VllmServerState.SUBMITTED
        assert srv.snapshot.job_id == "2398127"

    @pytest.mark.asyncio
    async def test_start_idempotent_when_already_running(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(state=VllmServerState.READY, job_id="1")

        submit_mock = mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.submit_vllm_job", new=AsyncMock()
        )
        await srv.start()
        submit_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_rejects_invalid_config_before_opening_ssh(self):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        ssh.open = AsyncMock()
        srv = VllmServer(
            ssh=ssh,
            config=_make_config(vllm_remote_workdir="relative/workdir"),
        )

        with pytest.raises(ValueError, match="vllm_remote_workdir"):
            await srv.start()

        ssh.open.assert_not_awaited()
        assert srv.snapshot.state == VllmServerState.FAILED


class TestVllmServerStop:
    @pytest.mark.asyncio
    async def test_stop_calls_scancel_and_transitions(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(state=VllmServerState.READY, job_id="42")

        scancel_mock = mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.scancel", new=AsyncMock()
        )
        await srv.stop()
        scancel_mock.assert_awaited_once_with(ssh, "42")
        # stop() now drives all cleanup synchronously (close tunnel, cancel
        # tasks, scancel) and lands on IDLE. Previously it stopped at
        # DRAINING and let the poller finish the work — fragile when the
        # poller had already exited (e.g. after FAILED).
        assert srv.snapshot.state == VllmServerState.IDLE
        assert srv.snapshot.job_id is None

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_idle(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        scancel_mock = mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.scancel", new=AsyncMock()
        )
        await srv.stop()
        scancel_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_cancels_probe_task(self, mocker):
        import asyncio

        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(state=VllmServerState.ALLOCATED, job_id="42")

        # Install a probe task that sleeps forever
        probe = asyncio.create_task(asyncio.sleep(60))
        srv._probe_task = probe

        mocker.patch("pipeline_app_hpc.hpc.lifecycle.scancel", new=AsyncMock())
        await srv.stop()
        # stop() awaits the cancellation internally, so by the time it
        # returns the probe task is already done. It's also been nulled out
        # on the server, so we hold our own reference.
        assert probe.cancelled()
        assert srv._probe_task is None


class TestVllmServerPollerTransitions:
    @pytest.mark.asyncio
    async def test_running_with_node_and_marker_transitions_to_allocated(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(state=VllmServerState.SUBMITTED, job_id="42")

        info = JobInfo("42", "RUNNING", "sphpc-gpu05", 3600, 60)
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.get_job_info",
            new=AsyncMock(return_value=info),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.fetch_log_tail",
            new=AsyncMock(return_value="some output\n##VLLM_PORT=43210##\n"),
        )
        # Tunnel.open succeeds
        tunnel_mock = MagicMock()
        tunnel_mock.open = AsyncMock()
        tunnel_mock.local_url = "http://127.0.0.1:30800"
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.VllmTunnel",
            return_value=tunnel_mock,
        )

        # Single poll iteration
        await srv._poll_once()

        assert srv.snapshot.state == VllmServerState.ALLOCATED
        assert srv.snapshot.node == "sphpc-gpu05"
        assert srv.snapshot.local_url == "http://127.0.0.1:30800"
        assert srv.snapshot.time_left_seconds == 3600

    @pytest.mark.asyncio
    async def test_completed_state_transitions_to_idle(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(state=VllmServerState.DRAINING, job_id="42")
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock

        info = JobInfo("42", "COMPLETED", None, 0, 100)
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.get_job_info",
            new=AsyncMock(return_value=info),
        )
        await srv._poll_once()
        assert srv.snapshot.state == VllmServerState.IDLE
        assert srv.snapshot.job_id is None
        # srv._tunnel is None after teardown — use local reference
        tunnel_mock.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_completed_state_transitions_to_failed(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(
            state=VllmServerState.READY,
            job_id="42",
            local_url="http://127.0.0.1:30800",
        )
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock

        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.get_job_info",
            new=AsyncMock(return_value=JobInfo("42", "COMPLETED", None, 0, 100)),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.fetch_log_tail",
            new=AsyncMock(return_value="server exited"),
        )

        await srv._poll_once()

        assert srv.snapshot.state == VllmServerState.FAILED
        assert "completed before vLLM was stopped" in (srv.snapshot.error or "")
        assert srv.snapshot.last_log_tail == "server exited"
        tunnel_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_state_closes_tunnel(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(
            state=VllmServerState.READY,
            job_id="42",
            local_url="http://127.0.0.1:30800",
        )
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock

        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.get_job_info",
            new=AsyncMock(return_value=JobInfo("42", "FAILED", None, 0, 100)),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.fetch_log_tail",
            new=AsyncMock(return_value="boom"),
        )

        await srv._poll_once()

        assert srv.snapshot.state == VllmServerState.FAILED
        assert srv.snapshot.local_url is None
        tunnel_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_slurm_terminal_failure_state_transitions_to_failed(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(
            state=VllmServerState.READY,
            job_id="42",
            local_url="http://127.0.0.1:30800",
        )
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock

        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.get_job_info",
            new=AsyncMock(return_value=JobInfo("42", "NODE_FAIL", None, 0, 100)),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.fetch_log_tail",
            new=AsyncMock(return_value="node died"),
        )

        await srv._poll_once()

        assert srv.snapshot.state == VllmServerState.FAILED
        assert "NODE_FAIL" in (srv.snapshot.error or "")
        assert srv.snapshot.last_log_tail == "node died"
        tunnel_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slurm_state", ["SUSPENDED", "STOPPED"])
    async def test_admin_suspended_job_transitions_to_failed(
        self, mocker, slurm_state
    ):
        # An admin-paused (SUSPENDED) or stopped job leaves the GPU process
        # paused while the SLURM record stays alive. Without this transition
        # the UI continues to show READY while requests through the tunnel
        # hang — the user has no signal anything is wrong.
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
        from pipeline_app_hpc.hpc.sbatch import JobInfo

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(
            state=VllmServerState.READY,
            job_id="42",
            local_url="http://127.0.0.1:30800",
        )
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock

        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.get_job_info",
            new=AsyncMock(return_value=JobInfo("42", slurm_state, None, 0, 100)),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.fetch_log_tail",
            new=AsyncMock(return_value="paused"),
        )

        await srv._poll_once()

        assert srv.snapshot.state == VllmServerState.FAILED
        assert slurm_state in (srv.snapshot.error or "")
        assert srv.snapshot.local_url is None
        tunnel_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sbatch_failure_transitions_to_failed(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        ssh.open = AsyncMock()
        ssh.run_bash = AsyncMock()
        srv = VllmServer(ssh=ssh, config=_make_config())

        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle._render_template",
            return_value="dummy_sbatch",
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.rsync_sbatch_template",
            new=AsyncMock(),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.submit_vllm_job",
            new=AsyncMock(side_effect=RuntimeError("over QoS limit")),
        )
        with pytest.raises(RuntimeError):
            await srv.start()
        assert srv.snapshot.state == VllmServerState.FAILED
        assert "over QoS limit" in (srv.snapshot.error or "")

    @pytest.mark.asyncio
    async def test_start_from_failed_closes_stale_tunnel(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        ssh.open = AsyncMock()
        ssh.run_bash = AsyncMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock
        srv._set(
            state=VllmServerState.FAILED,
            job_id="old",
            node="old-node",
            local_url="http://127.0.0.1:30800",
        )

        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle._render_template",
            return_value="dummy_sbatch",
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.rsync_sbatch_template",
            new=AsyncMock(),
        )
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.submit_vllm_job",
            new=AsyncMock(return_value="new"),
        )
        mocker.patch.object(srv, "_run_poller", new=AsyncMock())

        await srv.start()

        tunnel_mock.close.assert_awaited_once()
        assert srv.snapshot.state == VllmServerState.SUBMITTED
        assert srv.snapshot.job_id == "new"

    @pytest.mark.asyncio
    async def test_readiness_probe_exception_transitions_to_failed(self, mocker):
        from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState

        ssh = MagicMock()
        srv = VllmServer(ssh=ssh, config=_make_config())
        srv._set(
            state=VllmServerState.ALLOCATED,
            job_id="42",
            local_url="http://127.0.0.1:30800",
        )
        tunnel_mock = MagicMock()
        tunnel_mock.close = AsyncMock()
        srv._tunnel = tunnel_mock
        mocker.patch(
            "pipeline_app_hpc.hpc.lifecycle.wait_until_ready",
            new=AsyncMock(side_effect=RuntimeError("probe down")),
        )

        await srv._probe_ready()

        assert srv.snapshot.state == VllmServerState.FAILED
        assert "readiness probe failed" in (srv.snapshot.error or "")
        tunnel_mock.close.assert_awaited_once()
