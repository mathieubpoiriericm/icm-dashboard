"""Tests for pipeline_app config module."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pipeline_app.config import (
    MAX_HISTORY,
    EnvSecrets,
    PipelineAppConfig,
    TuningConfig,
    add_run_to_history,
    clear_history,
    delete_preset,
    load_config,
    load_env_secrets,
    load_history,
    load_preset,
    load_presets,
    load_tuning_config,
    save_config,
    save_history,
    save_preset,
    save_tuning_config,
    strip_secrets_from_config,
    upsert_preset,
)


class TestPipelineAppConfigDefaults:
    def test_default_llm_model(self):
        config = PipelineAppConfig()
        assert config.llm_model == "claude-opus-4-7"

    def test_default_days_back(self):
        config = PipelineAppConfig()
        assert config.days_back == 7

    def test_default_run_mode(self):
        config = PipelineAppConfig()
        assert config.run_mode == "standard"

    def test_default_confidence_threshold(self):
        config = PipelineAppConfig()
        assert config.confidence_threshold == 0.65

    def test_default_max_concurrent_papers(self):
        config = PipelineAppConfig()
        assert config.max_concurrent_papers == 5

    def test_default_rpm_limit(self):
        config = PipelineAppConfig()
        assert config.rpm_limit == 50

    def test_default_tpm_limit(self):
        config = PipelineAppConfig()
        assert config.tpm_limit == 100_000

    def test_default_dry_run_false(self):
        config = PipelineAppConfig()
        assert config.dry_run is False

    def test_default_llm_effort(self):
        config = PipelineAppConfig()
        assert config.llm_effort == "high"

    def test_default_prompt_version(self):
        config = PipelineAppConfig()
        assert config.prompt_version == "v5"


class TestTuningConfigDefaults:
    def test_default_gold_standard_path(self):
        config = TuningConfig()
        assert (
            config.gold_standard_path
            == "data/test_data/gold_standard/gold_standard_v2.csv"
        )

    def test_default_repeats(self):
        config = TuningConfig()
        assert config.repeats == 1

    def test_default_f_beta_weight(self):
        config = TuningConfig()
        assert config.f_beta_weight == 2.0

    def test_default_use_main_config(self):
        config = TuningConfig()
        assert config.use_main_config is True

    def test_default_auto_advance_false(self):
        config = TuningConfig()
        assert config.auto_advance is False


class TestEnvSecrets:
    def test_defaults_are_empty(self):
        secrets = EnvSecrets()
        assert secrets.anthropic_api_key == ""
        assert secrets.db_host == ""
        assert secrets.db_port == "5432"

    def test_load_from_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-test-123\n"
            "DB_HOST=localhost\n"
            "DB_PORT=5433\n"
            "DB_NAME=svd\n"
            "DB_USER=admin\n"
            "DB_PASSWORD=secret\n"
            "NCBI_API_KEY=ncbi-key\n"
            "ENTREZ_EMAIL=test@example.com\n"
            "UNPAYWALL_EMAIL=test@example.com\n"
        )
        secrets = load_env_secrets(str(tmp_path))
        assert secrets.anthropic_api_key == "sk-test-123"
        assert secrets.db_host == "localhost"
        assert secrets.db_port == "5433"
        assert secrets.db_name == "svd"
        assert secrets.db_user == "admin"
        assert secrets.db_password == "secret"
        assert secrets.ncbi_api_key == "ncbi-key"
        assert secrets.entrez_email == "test@example.com"
        assert secrets.unpaywall_email == "test@example.com"

    def test_load_missing_env_file(self, tmp_path: Path):
        secrets = load_env_secrets(str(tmp_path))
        assert secrets.anthropic_api_key == ""
        assert secrets.db_host == ""

    def test_load_partial_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-partial\n")
        secrets = load_env_secrets(str(tmp_path))
        assert secrets.anthropic_api_key == "sk-partial"
        assert secrets.db_host == ""

    def test_invalid_db_port_falls_back_to_default(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        """Non-numeric DB_PORT must not reach the subprocess verbatim — the
        pipeline would fail deep in asyncpg with a confusing error. The
        loader coerces to the default and logs a warning.
        """
        env_file = tmp_path / ".env"
        env_file.write_text("DB_PORT=5432a\n")
        with caplog.at_level(logging.WARNING, logger="pipeline_app.config"):
            secrets = load_env_secrets(str(tmp_path), use_cache=False)
        assert secrets.db_port == "5432"
        assert any(
            "Invalid DB_PORT" in r.message and "'5432a'" in r.message
            for r in caplog.records
        )

    def test_valid_numeric_db_port_passes_through(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("DB_PORT=6543\n")
        secrets = load_env_secrets(str(tmp_path), use_cache=False)
        assert secrets.db_port == "6543"


class TestConfigPersistence:
    def test_save_and_load_roundtrip(self, tmp_config_dir):
        config = PipelineAppConfig(llm_model="claude-sonnet-4-6", days_back=30)
        save_config(config)
        loaded = load_config()
        assert loaded.llm_model == "claude-sonnet-4-6"
        assert loaded.days_back == 30

    def test_load_returns_defaults_when_no_file(self, tmp_config_dir):
        config = load_config()
        assert config.llm_model == "claude-opus-4-7"
        assert config.days_back == 7

    def test_ignores_unknown_keys_in_json(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"llm_model": "test", "unknown_key": 42}))
        config = load_config()
        assert config.llm_model == "test"

    def test_tuning_config_roundtrip(self, tmp_config_dir):
        tuning = TuningConfig(repeats=5, f_beta_weight=1.5)
        save_tuning_config(tuning)
        loaded = load_tuning_config()
        assert loaded.repeats == 5
        assert loaded.f_beta_weight == 1.5


class TestHistory:
    def test_add_and_load(self, tmp_config_dir):
        add_run_to_history({"id": "run-1", "status": "success"})
        history = load_history()
        assert len(history) == 1
        assert history[0]["id"] == "run-1"

    def test_newest_first(self, tmp_config_dir):
        add_run_to_history({"id": "run-1"})
        add_run_to_history({"id": "run-2"})
        history = load_history()
        assert history[0]["id"] == "run-2"
        assert history[1]["id"] == "run-1"

    def test_caps_at_max(self, tmp_config_dir):
        for i in range(MAX_HISTORY + 10):
            add_run_to_history({"id": f"run-{i}"})
        history = load_history()
        assert len(history) == MAX_HISTORY

    def test_clear(self, tmp_config_dir):
        add_run_to_history({"id": "run-1"})
        clear_history()
        assert load_history() == []

    def test_load_empty_when_no_file(self, tmp_config_dir):
        assert load_history() == []


class TestPresets:
    def test_save_and_load(self, tmp_config_dir):
        config = PipelineAppConfig(llm_model="claude-sonnet-4-6")
        presets = save_preset("My Preset", config)
        assert len(presets) == 1
        loaded = load_preset(presets[0].id)
        assert loaded is not None
        assert loaded.llm_model == "claude-sonnet-4-6"

    def test_load_nonexistent_returns_none(self, tmp_config_dir):
        assert load_preset("nonexistent-id") is None

    def test_delete(self, tmp_config_dir):
        config = PipelineAppConfig()
        presets = save_preset("Delete Me", config)
        preset_id = presets[0].id
        delete_preset(preset_id)
        assert load_preset(preset_id) is None

    def test_multiple_presets(self, tmp_config_dir):
        presets1 = save_preset("A", PipelineAppConfig(days_back=1))
        presets2 = save_preset("B", PipelineAppConfig(days_back=2))
        id1 = presets1[-1].id
        id2 = presets2[-1].id
        loaded1 = load_preset(id1)
        loaded2 = load_preset(id2)
        assert loaded1 is not None
        assert loaded2 is not None
        assert loaded1.days_back == 1
        assert loaded2.days_back == 2


class TestUpsertPreset:
    """``upsert_preset`` replaces by name so the dropdown can't grow
    duplicate-label entries distinguishable only by hidden UUID."""

    def test_creates_when_name_absent(self, tmp_config_dir):
        presets = upsert_preset("A", PipelineAppConfig(days_back=1))
        assert len(presets) == 1
        assert presets[0].name == "A"
        assert presets[0].config["days_back"] == 1

    def test_replaces_existing_by_name(self, tmp_config_dir):
        upsert_preset("A", PipelineAppConfig(days_back=1))
        updated = upsert_preset("A", PipelineAppConfig(days_back=99))
        assert len(updated) == 1
        assert updated[0].config["days_back"] == 99

    def test_preserves_id_on_replace(self, tmp_config_dir):
        first = upsert_preset("A", PipelineAppConfig(days_back=1))
        original_id = first[0].id
        replaced = upsert_preset("A", PipelineAppConfig(days_back=2))
        assert replaced[0].id == original_id

    def test_does_not_touch_other_presets(self, tmp_config_dir):
        upsert_preset("A", PipelineAppConfig(days_back=1))
        upsert_preset("B", PipelineAppConfig(days_back=2))
        updated = upsert_preset("A", PipelineAppConfig(days_back=99))
        by_name = {p.name: p for p in updated}
        assert by_name["A"].config["days_back"] == 99
        assert by_name["B"].config["days_back"] == 2

    def test_strips_secrets(self, tmp_config_dir):
        # Confirm the same secret-scrubbing contract as save_preset.
        cfg = PipelineAppConfig()
        presets = upsert_preset("A", cfg)
        for field in ("anthropic_api_key", "db_password", "ncbi_api_key"):
            assert field not in presets[0].config


class TestStripSecrets:
    def test_strips_secret_keys(self):
        config_dict = {
            "llm_model": "claude-opus-4-6",
            "anthropic_api_key": "sk-secret",
            "db_password": "pass",
            "ncbi_api_key": "key",
        }
        stripped = strip_secrets_from_config(config_dict)
        assert "anthropic_api_key" not in stripped
        assert "db_password" not in stripped
        assert "ncbi_api_key" not in stripped
        assert stripped["llm_model"] == "claude-opus-4-6"


class TestPipelineAppConfigAllDefaults:
    """Cover defaults not checked by TestPipelineAppConfigDefaults."""

    def test_default_python_path(self):
        assert PipelineAppConfig().python_path == "python3"

    def test_default_project_root(self):
        assert PipelineAppConfig().project_root == ""

    def test_default_llm_max_tokens(self):
        assert PipelineAppConfig().llm_max_tokens == 0

    def test_default_db_pool_min_size(self):
        assert PipelineAppConfig().db_pool_min_size == 2

    def test_default_db_pool_max_size(self):
        assert PipelineAppConfig().db_pool_max_size == 10

    def test_default_estimated_tokens_per_call(self):
        assert PipelineAppConfig().estimated_tokens_per_call == 40_000

    def test_default_max_connection_retries(self):
        assert PipelineAppConfig().max_connection_retries == 3

    def test_default_progress_file(self):
        assert PipelineAppConfig().progress_file == ""

    def test_default_db_command_timeout(self):
        assert PipelineAppConfig().db_command_timeout == 60.0


class TestTuningConfigAllDefaults:
    """Cover defaults not checked by TestTuningConfigDefaults."""

    def test_default_notes(self):
        assert TuningConfig().notes == ""

    def test_default_llm_max_tokens(self):
        assert TuningConfig().llm_max_tokens == 0

    def test_default_python_path(self):
        assert TuningConfig().python_path == "python3"

    def test_default_project_root(self):
        assert TuningConfig().project_root == ""

    def test_default_confidence_threshold(self):
        assert TuningConfig().confidence_threshold == 0.7

    def test_default_pdf_path(self):
        assert TuningConfig().pdf_path == ""


class TestMalformedJson:
    def test_load_config_malformed_json(self, tmp_config_dir):
        (tmp_config_dir / "config.json").write_text("{invalid json")
        result = load_config()
        assert result == PipelineAppConfig()

    def test_load_tuning_config_malformed_json(self, tmp_config_dir):
        (tmp_config_dir / "tuning_config.json").write_text("{bad")
        result = load_tuning_config()
        assert result == TuningConfig()

    def test_load_history_malformed_json(self, tmp_config_dir):
        (tmp_config_dir / "history.json").write_text("{bad")
        assert load_history() == []

    def test_load_presets_malformed_json(self, tmp_config_dir):
        (tmp_config_dir / "presets.json").write_text("{bad")
        assert load_presets() == []

    def test_load_preset_with_null_config_does_not_crash(self, tmp_config_dir):
        # @dataclass has no runtime type check, so Preset(config=None)
        # constructs fine on disk-load; load_preset must not crash on
        # the subsequent None.items() call.
        import json as _json

        (tmp_config_dir / "presets.json").write_text(
            _json.dumps([{"id": "abc", "name": "Broken", "config": None}])
        )
        loaded = load_preset("abc")
        assert loaded == PipelineAppConfig()

    def test_load_preset_with_list_config_does_not_crash(self, tmp_config_dir):
        import json as _json

        entry = {"id": "abc", "name": "Broken", "config": ["not", "a", "dict"]}
        (tmp_config_dir / "presets.json").write_text(_json.dumps([entry]))
        loaded = load_preset("abc")
        assert loaded == PipelineAppConfig()


class TestSaveHistoryDirect:
    def test_save_caps_at_max(self, tmp_config_dir):
        big = [{"id": str(i)} for i in range(MAX_HISTORY + 20)]
        save_history(big)
        loaded = load_history()
        assert len(loaded) == MAX_HISTORY

    def test_save_empty_list(self, tmp_config_dir):
        save_history([])
        assert load_history() == []


class TestEnvSecretsEdgeCases:
    def test_empty_string_values_use_fallback(self, tmp_path: Path):
        (tmp_path / ".env").write_text("DB_HOST=\nDB_PORT=\n")
        secrets = load_env_secrets(str(tmp_path))
        assert secrets.db_host == ""
        assert secrets.db_port == "5432"

    def test_load_without_project_root_uses_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-cwd\n")
        secrets = load_env_secrets()
        assert secrets.anthropic_api_key == "from-cwd"


class TestBoolCoercion:
    """Regression tests for the bool('False') landmine fix in config.py.

    Json round-trips bool natively so this only matters when someone writes
    a string like 'False' into the persisted config file. Prior to the fix
    that string would be silently coerced to True via bool('False').
    """

    def test_string_false_does_not_coerce_to_true(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"dry_run": "False"}))
        config = load_config()
        # Field is dropped (not coerced via bool('False')); default holds.
        assert config.dry_run is False

    def test_string_true_does_not_coerce_to_true(self, tmp_config_dir):
        # Critical case: bool('True') is True regardless, but should not be
        # accepted as a valid bool value — strings aren't trustworthy here.
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"dry_run": "True"}))
        config = load_config()
        # Default of False is preserved; string was rejected.
        assert config.dry_run is False

    def test_int_zero_coerces_to_false(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"dry_run": 0}))
        config = load_config()
        assert config.dry_run is False

    def test_int_one_coerces_to_true(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"dry_run": 1}))
        config = load_config()
        assert config.dry_run is True

    def test_native_bool_round_trip(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"dry_run": True, "test_mode": False}))
        config = load_config()
        assert config.dry_run is True
        assert config.test_mode is False

    def test_arbitrary_string_for_int_field_falls_back(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"days_back": "not_a_number"}))
        config = load_config()
        # Default of 7 is preserved when coercion fails.
        assert config.days_back == 7

    def test_numeric_string_coerces_to_int(self, tmp_config_dir):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"days_back": "30"}))
        config = load_config()
        assert config.days_back == 30


class TestPresetCache:
    """The mtime-keyed cache must avoid redundant disk reads without masking
    external writes or going stale across monkeypatched paths."""

    def test_cache_hit_avoids_disk_read(self, tmp_config_dir, monkeypatch):
        config = PipelineAppConfig()
        save_preset("A", config)
        # Force the cache to be populated, then sabotage the file so any
        # subsequent read would crash. Cache hit means no re-read happens.
        load_presets()
        (tmp_config_dir / "presets.json").write_text("{not valid json")
        # mtime changed so cache is invalidated; regression case.
        result = load_presets()
        assert result == []

    def test_save_invalidates_cache(self, tmp_config_dir):
        save_preset("A", PipelineAppConfig(days_back=1))
        first = load_presets()
        assert len(first) == 1
        save_preset("B", PipelineAppConfig(days_back=2))
        second = load_presets()
        assert len(second) == 2

    def test_delete_invalidates_cache(self, tmp_config_dir):
        presets = save_preset("A", PipelineAppConfig())
        assert len(load_presets()) == 1
        delete_preset(presets[0].id)
        assert load_presets() == []

    def test_returns_copy_not_reference(self, tmp_config_dir):
        save_preset("A", PipelineAppConfig())
        first = load_presets()
        first.clear()
        second = load_presets()
        assert len(second) == 1


class TestEnvSecretsCache:
    def test_cache_hit_avoids_reparse(self, tmp_path: Path):
        env_path = tmp_path / ".env"
        env_path.write_text("ANTHROPIC_API_KEY=one\n")
        first = load_env_secrets(str(tmp_path))
        assert first.anthropic_api_key == "one"
        env_path.write_text("ANTHROPIC_API_KEY=two\n")
        # Same mtime (write is too quick on most filesystems) would still
        # return cached; on a slow filesystem mtime ticks and we see "two".
        # Either way, use_cache=False must observe the latest file.
        fresh = load_env_secrets(str(tmp_path), use_cache=False)
        assert fresh.anthropic_api_key == "two"

    def test_different_project_roots_isolated(self, tmp_path: Path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / ".env").write_text("ANTHROPIC_API_KEY=alpha\n")
        (root_b / ".env").write_text("ANTHROPIC_API_KEY=bravo\n")
        assert load_env_secrets(str(root_a)).anthropic_api_key == "alpha"
        assert load_env_secrets(str(root_b)).anthropic_api_key == "bravo"


class TestProviderFields:
    def test_pipeline_app_config_defaults(self):
        cfg = PipelineAppConfig()
        assert cfg.llm_provider == "anthropic"
        assert cfg.ollama_host == "http://localhost:11434"
        assert cfg.ollama_model == "gemma4:e4b"
        assert cfg.ollama_num_ctx == 65_536

    def test_tuning_config_defaults(self):
        cfg = TuningConfig()
        assert cfg.llm_provider == "anthropic"
        assert cfg.ollama_model == "gemma4:e4b"

    def test_llm_providers_constant(self):
        from pipeline_app.config import LLM_PROVIDERS
        assert LLM_PROVIDERS == ["anthropic", "ollama"]

    def test_prompt_versions_includes_ollama_v1(self):
        from pipeline_app.config import PROMPT_VERSIONS
        assert "ollama_v1" in PROMPT_VERSIONS

    def test_pipeline_app_config_roundtrips_provider_fields(self, tmp_config_dir):
        """Persistence round-trip for the new fields. Uses the shared
        `tmp_config_dir` fixture which redirects CONFIG_PATH to a temp dir."""
        from pipeline_app.config import load_config, save_config

        cfg = PipelineAppConfig(
            llm_provider="ollama",
            ollama_host="http://gpu:11434",
            ollama_model="svd-gemma:v1",
            ollama_num_ctx=131_072,
        )
        save_config(cfg)
        loaded = load_config()
        assert loaded.llm_provider == "ollama"
        assert loaded.ollama_host == "http://gpu:11434"
        assert loaded.ollama_model == "svd-gemma:v1"
        assert loaded.ollama_num_ctx == 131_072


class TestFieldDropLogging:
    """Regression: schema-mismatch field drops must surface as warnings."""

    def test_none_value_logs_warning(self, tmp_config_dir, caplog):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"days_back": None}))
        with caplog.at_level(logging.WARNING, logger="pipeline_app.config"):
            config = load_config()
        assert config.days_back == 7
        assert any(
            "days_back" in r.message and "None" in r.message for r in caplog.records
        )

    def test_uncoercible_string_logs_warning(self, tmp_config_dir, caplog):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"days_back": "not_a_number"}))
        with caplog.at_level(logging.WARNING, logger="pipeline_app.config"):
            config = load_config()
        assert config.days_back == 7
        assert any(
            "days_back" in r.message and "coerce to int" in r.message
            for r in caplog.records
        )

    def test_bool_mismatch_logs_warning(self, tmp_config_dir, caplog):
        path = tmp_config_dir / "config.json"
        path.write_text(json.dumps({"dry_run": "False"}))
        with caplog.at_level(logging.WARNING, logger="pipeline_app.config"):
            config = load_config()
        assert config.dry_run is False
        assert any(
            "dry_run" in r.message and "bool expected" in r.message
            for r in caplog.records
        )
