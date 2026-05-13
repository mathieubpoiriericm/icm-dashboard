"""HpcCard: Start/Stop vLLM, state chip, job ID, allocated node, log tail."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from nicegui import context, ui

from pipeline_app_hpc.hpc.lifecycle import VllmServerState

if TYPE_CHECKING:
    from pipeline_app_hpc.hpc.lifecycle import VllmServer, VllmServerSnapshot

_STATE_LABEL: dict[str, str] = {
    "idle": "Stopped",
    "submitted": "Submitted",
    "allocated": "Allocated",
    "ready": "Ready",
    "draining": "Stopping",
    "failed": "Failed",
}


def render_hpc_card(server: VllmServer) -> None:
    """Render the HPC card with live binding to a VllmServer."""
    with ui.card().classes("w-full q-pa-md theme-card hpc-card"):
        ui.label("vLLM on HPC").classes("section-header")

        with ui.row().classes("items-center gap-md no-wrap"):
            # ui.label, not ui.element('span'): only ui.label has a `.text`
            # setter that propagates to the DOM.
            chip = ui.label("").classes("hpc-state-chip")
            meta_lbl = ui.label("").classes("text-muted")

        time_lbl = ui.label("").classes("text-muted")
        url_lbl = ui.label("").classes("text-muted hpc-url")

        with ui.expansion("Last log").classes("w-full hpc-log"):
            # ui.label + Tailwind whitespace-pre-wrap to keep newlines that
            # the prior <pre> tag preserved via browser default styling.
            log_pre = ui.label("").classes(
                "hpc-log-pre whitespace-pre-wrap font-mono text-xs"
            )

        error_lbl = ui.label("").classes("text-negative")

        async def _start() -> None:
            try:
                ui.notify(
                    "Starting vLLM; complete any SSH prompts in the terminal",
                    color="info",
                )
                await server.start()
            except Exception as e:
                ui.notify(str(e), color="negative")

        async def _stop() -> None:
            try:
                await server.stop()
            except Exception as e:
                ui.notify(str(e), color="negative")

        with ui.row().classes("gap-sm"):
            start_btn = (
                ui.button("Start vLLM", on_click=_start, icon="play_arrow")
                .props("unelevated")
                .classes("btn-primary")
            )
            stop_btn = (
                ui.button("Stop vLLM", on_click=_stop, icon="stop")
                .props("outline")
                .classes("btn-warning")
            )

        # Track the last-applied state so refresh callbacks issued by the
        # 5s poll can skip DOM mutations when nothing observable changed —
        # the time_left_seconds countdown decrements every tick, defeating
        # the snapshot equality guard upstream.
        last_state = ""
        last_time_left: int | None = None

        def _refresh(snap: VllmServerSnapshot) -> None:
            nonlocal last_state, last_time_left

            state_name = snap.state.value
            if state_name != last_state:
                chip.classes(replace=f"hpc-state-chip hpc-state-{state_name}")
                chip.text = _STATE_LABEL.get(state_name, state_name)
                chip.update()
                is_terminal = snap.state in (
                    VllmServerState.IDLE,
                    VllmServerState.FAILED,
                )
                start_btn.set_visibility(is_terminal)
                stop_btn.set_visibility(not is_terminal)
                last_state = state_name

            parts = []
            if snap.job_id:
                parts.append(f"job {snap.job_id}")
            if snap.node:
                parts.append(snap.node)
            meta_lbl.text = " · ".join(parts)

            if snap.time_left_seconds != last_time_left:
                if snap.time_left_seconds is not None:
                    h, rem = divmod(snap.time_left_seconds, 3600)
                    m, s = divmod(rem, 60)
                    time_lbl.text = f"Time left: {h:02d}:{m:02d}:{s:02d}"
                else:
                    time_lbl.text = ""
                last_time_left = snap.time_left_seconds

            url_lbl.text = f"Endpoint: {snap.local_url}" if snap.local_url else ""
            log_pre.text = snap.last_log_tail or ""
            error_lbl.text = snap.error or ""

        # Initial paint + subscription
        _refresh(server.snapshot)
        # Capture the dispose handle so the closure (which holds references
        # to per-client DOM elements) is unregistered when this client
        # disconnects. Without cleanup the subscriber list grows on every
        # page render and stale callbacks fire against dead UI elements.
        dispose = server.subscribe(_refresh)
        with suppress(RuntimeError, AttributeError):
            context.client.on_disconnect(dispose)
