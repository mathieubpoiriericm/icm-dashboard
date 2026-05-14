"""Tests for pipeline_app_hpc.config module."""

from __future__ import annotations

from pathlib import Path


class TestHpcAppConfigDefaults:
    def test_default_ssh_alias(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().ssh_alias == "icm-hpc"

    def test_default_run_mode(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().run_mode == "local_pdfs"

    def test_default_prompt_version(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().prompt_version == "gemma_v1"

    def test_default_vllm_local_port(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().vllm_local_port == 30800

    def test_default_vllm_account(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().vllm_account == "debette-chabriat"

    def test_default_vllm_partition(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().vllm_partition == "gpu-all"

    def test_default_vllm_qos(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().vllm_qos == "qos6"

    def test_default_vllm_base_model(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert (
            HpcAppConfig().vllm_base_model == "unsloth/gemma-4-31b-it-unsloth-bnb-4bit"
        )

    def test_default_vllm_readiness_timeout(self):
        from pipeline_app_hpc.config import HpcAppConfig

        assert HpcAppConfig().vllm_readiness_timeout == 900.0

    def test_no_anthropic_or_db_fields(self):
        from pipeline_app_hpc.config import HpcAppConfig

        cfg = HpcAppConfig()
        assert not hasattr(cfg, "anthropic_api_key")
        assert not hasattr(cfg, "db_host")
        assert not hasattr(cfg, "llm_provider")


class TestEnvSecrets:
    def test_only_ncbi_fields(self):
        from pipeline_app_hpc.config import EnvSecrets

        s = EnvSecrets()
        assert s.ncbi_api_key == ""
        assert s.entrez_email == ""
        assert not hasattr(s, "anthropic_api_key")
        assert not hasattr(s, "db_host")


class TestSaveLoadRoundTrip:
    def test_round_trip(self, tmp_config_dir: Path):
        from pipeline_app_hpc.config import HpcAppConfig, load_config, save_config

        cfg = HpcAppConfig(ssh_alias="my-cluster", vllm_local_port=31000)
        save_config(cfg)
        loaded = load_config()
        assert loaded.ssh_alias == "my-cluster"
        assert loaded.vllm_local_port == 31000

    def test_save_normalizes_integer_fields_from_number_inputs(
        self, tmp_config_dir: Path
    ):
        import json

        from pipeline_app_hpc.config import HpcAppConfig, load_config, save_config

        cfg = HpcAppConfig(vllm_local_port=30800)
        cfg.__dict__["vllm_local_port"] = 31000.0
        save_config(cfg)

        raw = json.loads((tmp_config_dir / "config.json").read_text())
        loaded = load_config()
        assert raw["vllm_local_port"] == 31000
        assert loaded.vllm_local_port == 31000
        assert isinstance(loaded.vllm_local_port, int)


class TestStripSecrets:
    def test_strips_ncbi(self):
        from pipeline_app_hpc.config import strip_secrets_from_config

        d = {"ssh_alias": "a", "ncbi_api_key": "secret", "entrez_email": "e@e"}
        out = strip_secrets_from_config(d)
        assert "ssh_alias" in out
        assert "ncbi_api_key" not in out
        assert "entrez_email" not in out


class TestLoadEnvSecrets:
    def test_reads_from_env_file(self, tmp_path: Path):
        from pipeline_app_hpc.config import load_env_secrets

        env = tmp_path / ".env"
        env.write_text("NCBI_API_KEY=abc\nENTREZ_EMAIL=foo@bar\n")
        s = load_env_secrets(str(tmp_path), use_cache=False)
        assert s.ncbi_api_key == "abc"
        assert s.entrez_email == "foo@bar"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        from pipeline_app_hpc.config import load_env_secrets

        s = load_env_secrets(str(tmp_path), use_cache=False)
        assert s.ncbi_api_key == ""


class TestHistory:
    def test_load_history_drops_non_dict_entries(self, tmp_config_dir: Path):
        import json

        from pipeline_app_hpc.config import load_history

        (tmp_config_dir / "history.json").write_text(
            json.dumps([{"id": "ok"}, None, "bad", ["bad"]])
        )

        assert load_history() == [{"id": "ok"}]


class TestPresets:
    def test_load_preset_returns_none_for_missing_config(self, tmp_config_dir: Path):
        import json

        from pipeline_app_hpc.config import load_preset

        (tmp_config_dir / "presets.json").write_text(
            json.dumps([{"id": "broken", "name": "Broken"}])
        )

        assert load_preset("broken") is None

    def test_load_preset_returns_none_for_non_dict_config(self, tmp_config_dir: Path):
        import json

        from pipeline_app_hpc.config import load_preset

        (tmp_config_dir / "presets.json").write_text(
            json.dumps([{"id": "broken", "name": "Broken", "config": []}])
        )

        assert load_preset("broken") is None
