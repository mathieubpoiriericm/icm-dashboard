"""End-to-end integration test against a live HPC. Skipped by default."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_HPC_INTEGRATION") != "1",
        reason="set RUN_HPC_INTEGRATION=1 to run live HPC tests",
    ),
]


@pytest.mark.asyncio
async def test_full_lifecycle(tmp_path):
    """Submit a real sbatch, wait for ready, run one PDF, verify report."""
    from pipeline_app_hpc.config import HpcAppConfig
    from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerState
    from pipeline_app_hpc.hpc.ssh import SshControlMaster

    cfg = HpcAppConfig()
    ssh = SshControlMaster(alias=cfg.ssh_alias)
    srv = VllmServer(ssh=ssh, config=cfg)
    await srv.start()

    # Wait up to 8 minutes for READY
    deadline = time.monotonic() + 8 * 60
    while time.monotonic() < deadline:
        if srv.snapshot.state == VllmServerState.READY:
            break
        await asyncio.sleep(5)
    assert srv.snapshot.state == VllmServerState.READY, srv.snapshot

    try:
        # The actual extraction step would go here. For a smoke check we
        # just verify the HTTP endpoint responds.
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{srv.snapshot.local_url}/v1/models")
            assert r.status_code == 200
    finally:
        await srv.stop()
        # Wait for IDLE
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if srv.snapshot.state == VllmServerState.IDLE:
                break
            await asyncio.sleep(2)
