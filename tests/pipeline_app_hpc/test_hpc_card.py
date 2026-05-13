"""Smoke tests for HpcCard component."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _snapshot(state):
    from pipeline_app_hpc.hpc.lifecycle import VllmServerSnapshot

    return VllmServerSnapshot(
        state=state,
        job_id="42" if state.value != "idle" else None,
        node="sphpc-gpu05" if state.value in ("allocated", "ready") else None,
        local_url=(
            "http://127.0.0.1:30800" if state.value in ("allocated", "ready") else None
        ),
        time_left_seconds=3600 if state.value in ("allocated", "ready") else None,
        error=None if state.value != "failed" else "boom",
        last_log_tail="some log",
    )


class TestHpcCardRender:
    @pytest.mark.parametrize(
        "state_name",
        [
            "IDLE",
            "SUBMITTED",
            "ALLOCATED",
            "READY",
            "DRAINING",
            "FAILED",
        ],
    )
    def test_renders_for_each_state(self, state_name):
        from nicegui import ui
        from pipeline_app_hpc.components.hpc_card import render_hpc_card
        from pipeline_app_hpc.hpc.lifecycle import VllmServerState

        srv = MagicMock()
        state = getattr(VllmServerState, state_name)
        srv.snapshot = _snapshot(state)
        srv.subscribe = MagicMock(return_value=lambda: None)
        # Should construct without raising
        with ui.element("div"):
            render_hpc_card(srv)
