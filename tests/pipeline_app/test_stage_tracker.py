"""Tests for stage_tracker pure helpers and status configuration."""

from __future__ import annotations

from typing import get_args

import pytest
from pipeline_app.components.stage_tracker import (
    _STATUS_CONFIG,
    StageStatus,
    format_duration,
)


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0.0, "0.0s"),
            (1.0, "1.0s"),
            (12.4, "12.4s"),
            (59.9, "59.9s"),
        ],
    )
    def test_sub_minute_uses_seconds(self, seconds: float, expected: str):
        assert format_duration(seconds) == expected

    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (60, "1m 00s"),
            (65, "1m 05s"),
            (125, "2m 05s"),
            (3599, "59m 59s"),
            (3600, "60m 00s"),
        ],
    )
    def test_minute_plus_uses_mm_ss(self, seconds: float, expected: str):
        assert format_duration(seconds) == expected


class TestStatusConfig:
    def test_covers_every_status(self):
        assert set(_STATUS_CONFIG) == set(get_args(StageStatus))

    def test_all_icon_names_look_like_material_icons(self):
        for status, cfg in _STATUS_CONFIG.items():
            assert cfg.icon_name.islower() or "_" in cfg.icon_name, (
                f"{status} → {cfg.icon_name!r} does not look like a Material icon"
            )

    def test_all_icon_classes_use_icon_prefix(self):
        for cfg in _STATUS_CONFIG.values():
            assert cfg.icon_cls.startswith("icon-")

    def test_only_expected_statuses_have_label_class(self):
        labelled = {s for s, cfg in _STATUS_CONFIG.items() if cfg.label_cls}
        assert labelled == {"running", "completed", "failed", "cancelled"}
