"""Smoke tests for pipeline_app_hpc.main."""

from __future__ import annotations

from unittest.mock import MagicMock


class TestBuildShutdownHandler:
    def test_handler_is_callable(self):
        from pipeline_app_hpc.main import build_shutdown_handler

        lock = MagicMock()
        tuning = MagicMock()
        srv = MagicMock()
        ssh = MagicMock()
        h = build_shutdown_handler(lock, tuning, srv, ssh)
        assert callable(h)


class TestSetupPagesRoutesPresent:
    def test_routes_registered(self, mocker):
        from pipeline_app_hpc import main as main_mod

        # Patch ui.page so registrations don't reach NiceGUI's globals
        page_mock = mocker.patch.object(main_mod, "ui")
        page_mock.page.side_effect = lambda *a, **kw: lambda f: f
        page_mock.left_drawer.return_value.__enter__ = MagicMock()
        page_mock.left_drawer.return_value.__exit__ = MagicMock()
        page_mock.header.return_value.__enter__ = MagicMock()
        page_mock.header.return_value.__exit__ = MagicMock()
        page_mock.footer.return_value.__enter__ = MagicMock()
        page_mock.footer.return_value.__exit__ = MagicMock()

        main_mod.setup_pages(
            lock=MagicMock(),
            tuning_runner=MagicMock(),
            pipeline_runner=MagicMock(),
            vllm_server=MagicMock(),
        )
        # Verify ui.page was called for each route we expect
        called_paths = {c.args[0] for c in page_mock.page.call_args_list}
        assert "/" in called_paths
        assert "/history" in called_paths
        assert "/tuning" in called_paths
