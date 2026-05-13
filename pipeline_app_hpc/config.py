"""Configuration dataclasses, persistence, and .env loading for the HPC pipeline app."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import uuid
from contextlib import suppress
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.json"
PRESETS_PATH = CONFIG_DIR / "presets.json"
TUNING_CONFIG_PATH = CONFIG_DIR / "tuning_config.json"
MAX_HISTORY: int = 100
SENSITIVE_FIELDS: frozenset[str] = frozenset({"ncbi_api_key", "entrez_email"})
PROMPT_VERSIONS: list[str] = ["gemma_v5", "gemma_v4", "ollama_v1"]


# ---- Dataclasses ----


@dataclass
class HpcAppConfig:
    """Non-sensitive HPC pipeline settings, persisted to config.json."""

    # ---- env (Mac) ----
    python_path: str = "python3"
    project_root: str = ""
    progress_file: str = ""

    # ---- pipeline ----
    run_mode: str = "local_pdfs"
    local_pdfs_path: str = ""
    skip_validation: bool = False
    prompt_version: str = "ollama_v1"
    confidence_threshold: float = 0.65
    max_concurrent_papers: int = 5
    rpm_limit: int = 50
    tpm_limit: int = 100_000
    estimated_tokens_per_call: int = 40_000
    ncbi_rate_limit: int = 10
    max_paper_text_chars: int = 100_000
    llm_max_tokens: int = 0
    max_retries: int = 1
    max_rate_limit_retries: int = 6
    rate_limit_retry_delay: float = 1.0
    max_connection_retries: int = 3
    connection_retry_delay: float = 2.0

    # ---- SSH ----
    ssh_alias: str = "icm-hpc"
    ssh_socket_path: str = ""
    vllm_local_port: int = 30800

    # ---- HPC remote paths ----
    vllm_remote_workdir: str = "/network/iss/debette/users/mathieu.poirier/csvd-hpc"
    vllm_remote_log_dir: str = (
        "/network/iss/debette/users/mathieu.poirier/csvd-hpc/logs"
    )
    vllm_remote_venv_path: str = "/network/iss/debette/users/mathieu.poirier/.venv-vllm"
    vllm_hf_home: str = (
        "/network/iss/debette/users/mathieu.poirier/hf-cache/huggingface"
    )

    # ---- vLLM model ----
    vllm_base_model: str = "unsloth/gemma-4-31b-it-unsloth-bnb-4bit"
    vllm_adapter_path: str = ""
    vllm_adapter_name: str = "svd"
    vllm_max_model_len: int = 16384
    vllm_max_lora_rank: int = 16
    vllm_quantization: str = "bitsandbytes"

    # ---- SLURM ----
    vllm_account: str = "debette-chabriat"
    vllm_partition: str = "gpu-all"
    vllm_qos: str = "qos6"
    vllm_time_limit: str = "04:00:00"
    vllm_cpus_per_task: int = 14
    vllm_mem: str = "64G"

    # ---- timeouts ----
    vllm_readiness_timeout: float = 900.0


@dataclass
class TuningConfig:
    """Tuning experiment settings; vllm-only."""

    pdf_path: str = ""
    gold_standard_path: str = "data/test_data/gold_standard/gold_standard_v2.csv"
    confidence_threshold: float = 0.7
    repeats: int = 1
    auto_advance: bool = False
    f_beta_weight: float = 2.0
    notes: str = ""
    use_main_config: bool = True
    prompt_version: str = "ollama_v1"
    vllm_base_model: str = "unsloth/gemma-4-31b-it-unsloth-bnb-4bit"
    vllm_adapter_path: str = ""
    vllm_adapter_name: str = "svd"
    vllm_max_model_len: int = 16384
    python_path: str = "python3"
    project_root: str = ""


@dataclass
class EnvSecrets:
    """Credentials loaded from .env. Never persisted by the app."""

    ncbi_api_key: str = ""
    entrez_email: str = ""


# ---- .env loading ----


# Keyed by project_root → (mtime, EnvSecrets). mtime=-1.0 marks a "file
# missing" cache entry so a subsequent .env creation triggers a re-read.
_secrets_cache: dict[str, tuple[float, EnvSecrets]] = {}


def load_env_secrets(
    project_root: str = "",
    *,
    use_cache: bool = True,
) -> EnvSecrets:
    """Read credentials from .env file in project root.

    Results are cached per ``project_root`` keyed on the .env file's mtime so
    repeated page renders don't re-parse the same file. Pass
    ``use_cache=False`` right before spawning a pipeline subprocess to force a
    fresh read and catch any edits made during the session.
    """
    env_path = Path(project_root) / ".env" if project_root else Path(".env")

    try:
        mtime = env_path.stat().st_mtime
    except OSError:
        mtime = -1.0

    if use_cache:
        cached = _secrets_cache.get(project_root)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    values = dotenv_values(env_path) if mtime >= 0 else {}
    secrets = EnvSecrets(
        ncbi_api_key=values.get("NCBI_API_KEY") or "",
        entrez_email=values.get("ENTREZ_EMAIL") or "",
    )
    _secrets_cache[project_root] = (mtime, secrets)
    return secrets


# ---- Config persistence ----


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    # Track fd ownership explicitly: if os.fdopen raises before taking it,
    # the raw fd would leak and gradually exhaust the process fd table.
    fd_owned = True
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd_owned = False
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        if fd_owned:
            with suppress(OSError):
                os.close(fd)
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def _filter_dataclass_fields(data: Any, cls: type) -> dict[str, Any]:
    """Keep only keys that match dataclass field names, with type coercion."""
    # Guard non-dict inputs: dataclass constructors have no runtime type
    # checks, so a hand-edited presets.json with `"config": null` or a list
    # constructs Preset(config=...) cleanly and crashes here on .items().
    if not isinstance(data, dict):
        logger.warning(
            "Expected dict for %s, got %s; using defaults",
            cls.__name__,
            type(data).__name__,
        )
        return {}
    valid_fields = {f.name: f for f in fields(cls)}
    result = {}
    for k, v in data.items():
        if k not in valid_fields:
            # Debug-level: noisy on legitimate rename/upgrade paths.
            logger.debug("Ignoring unknown field %r for %s", k, cls.__name__)
            continue
        coerced = _coerce_dataclass_value(cls, k, v, valid_fields[k])
        if coerced is _DROP_FIELD:
            continue
        result[k] = coerced
    return result


_DROP_FIELD = object()


def _expected_field_type(field: Any) -> type | None:
    """Infer a runtime type from a dataclass field's default value."""
    if field.default is not MISSING:
        return type(field.default)
    if field.default_factory is not MISSING:
        try:
            return type(field.default_factory())
        except TypeError:
            return None
    return None


def _coerce_dataclass_value(
    cls: type,
    key: str,
    value: Any,
    field: Any,
) -> Any:
    """Coerce one JSON value to a dataclass field's default-derived type."""
    if value is None:
        logger.warning(
            "Field %s.%s dropped: got None (non-nullable)",
            cls.__name__,
            key,
        )
        return _DROP_FIELD

    expected_type = _expected_field_type(field)
    if expected_type is None:
        return value

    # bool is a subclass of int, so isinstance(True, int) is True. Rejecting
    # it for non-bool fields avoids silently accepting JSON true/false as 1/0.
    if expected_type is not bool and isinstance(value, bool):
        logger.warning(
            "Field %s.%s=%r dropped (%s expected, got bool)",
            cls.__name__,
            key,
            value,
            expected_type.__name__,
        )
        return _DROP_FIELD

    if isinstance(value, expected_type):
        return value

    if expected_type is bool:
        if isinstance(value, int):
            return bool(value)
        logger.warning(
            "Field %s.%s=%r dropped (bool expected, got %s)",
            cls.__name__,
            key,
            value,
            type(value).__name__,
        )
        return _DROP_FIELD

    if expected_type is int and isinstance(value, float):
        if value.is_integer():
            return int(value)
        logger.warning(
            "Field %s.%s=%r dropped (int expected, got non-integral float)",
            cls.__name__,
            key,
            value,
        )
        return _DROP_FIELD

    try:
        return expected_type(value)
    except (TypeError, ValueError):
        logger.warning(
            "Field %s.%s=%r dropped (could not coerce to %s)",
            cls.__name__,
            key,
            value,
            expected_type.__name__,
        )
        return _DROP_FIELD


def _load_dataclass[T](path: Path, cls: type[T]) -> T:
    """Load a dataclass from JSON, or return defaults."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cls()
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s, using defaults: %s", path, e)
        return cls()
    if not isinstance(data, dict):
        # Valid JSON can still decode to a list/null/string — guard so the
        # next .items() call doesn't raise AttributeError and brick startup.
        logger.warning(
            "Expected dict in %s, got %s; using defaults",
            path,
            type(data).__name__,
        )
        return cls()
    return cls(**_filter_dataclass_fields(data, cls))


# Keyed by (path, mtime_ns) so saves bust the cache automatically. Each
# call still returns a fresh instance (replace()) so concurrent page renders
# can't observe each other's edits via NiceGUI's two-way binding.
_dataclass_cache: dict[Path, tuple[int, Any]] = {}


def _load_dataclass_cached[T](path: Path, cls: type[T]) -> T:
    """mtime-cached variant of _load_dataclass for hot page-render paths.

    Returns a shallow copy on cache hits so concurrent page renders can't
    observe each other's NiceGUI-binding mutations via the cached instance.
    """
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = -1
    cached = _dataclass_cache.get(path)
    if cached is not None and cached[0] == mtime_ns and isinstance(cached[1], cls):
        return copy.copy(cached[1])
    obj = _load_dataclass(path, cls)
    _dataclass_cache[path] = (mtime_ns, obj)
    return copy.copy(obj)


def _normalize_dataclass_in_place(obj: Any) -> None:
    """Coerce a dataclass instance using the same rules as JSON loading."""
    cls = type(obj)
    normalized = cls(**_filter_dataclass_fields(asdict(obj), cls))
    for f in fields(cls):
        setattr(obj, f.name, getattr(normalized, f.name))


def _save_dataclass(path: Path, obj: Any) -> None:
    """Save a dataclass to JSON."""
    _normalize_dataclass_in_place(obj)
    _atomic_write(path, json.dumps(asdict(obj), indent=2))


def load_config() -> HpcAppConfig:
    """Load HPC pipeline config from JSON, or return defaults.

    Cached on the file's mtime — every page render calls this, so the
    full read + JSON parse + coerce pipeline runs only when the file
    actually changes."""
    return _load_dataclass_cached(CONFIG_PATH, HpcAppConfig)


def save_config(config: HpcAppConfig) -> None:
    """Save HPC pipeline config to JSON."""
    _save_dataclass(CONFIG_PATH, config)


def load_tuning_config() -> TuningConfig:
    """Load tuning config from JSON, or return defaults."""
    return _load_dataclass_cached(TUNING_CONFIG_PATH, TuningConfig)


def save_tuning_config(config: TuningConfig) -> None:
    """Save tuning config to JSON."""
    _save_dataclass(TUNING_CONFIG_PATH, config)


# ---- History ----


def load_history() -> list[dict[str, Any]]:
    """Load run history from JSON."""
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read history, returning empty: %s", e)
        return []
    if not isinstance(data, list):
        return []
    return [record for record in data if isinstance(record, dict)]


def save_history(history: list[dict[str, Any]]) -> None:
    """Save run history to JSON, capped at MAX_HISTORY."""
    _atomic_write(HISTORY_PATH, json.dumps(history[:MAX_HISTORY], indent=2))


def add_run_to_history(record: dict[str, Any]) -> None:
    """Prepend a run record to history."""
    history = load_history()
    history.insert(0, record)
    save_history(history)


def clear_history() -> None:
    """Delete all run history."""
    save_history([])


# ---- Presets ----


@dataclass
class Preset:
    """A named configuration snapshot.

    ``config`` is typed Optional because a hand-edited presets.json can
    omit the key entirely — we keep the preset so the name can still be
    listed / deleted, but downstream consumers must guard against None
    before treating it as a dict.
    """

    id: str
    name: str
    config: dict[str, Any] | None = None


# Keyed by (PRESETS_PATH, mtime_ns): nanosecond resolution so two writes
# inside the same wall-clock second can't collide on filesystems with
# 1-second mtime resolution (HFS+, FAT32). Cleared on any write via
# _save_presets so internal updates bust the cache regardless of timing.
_presets_cache: tuple[Path, int, list[Preset]] | None = None


def load_presets() -> list[Preset]:
    """Load all presets from JSON (mtime-cached)."""
    global _presets_cache
    try:
        mtime_ns = PRESETS_PATH.stat().st_mtime_ns
    except OSError:
        _presets_cache = None
        return []

    if _presets_cache is not None:
        cached_path, cached_mtime_ns, cached_presets = _presets_cache
        if cached_path == PRESETS_PATH and cached_mtime_ns == mtime_ns:
            return _copy_presets(cached_presets)

    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _presets_cache = None
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read presets, returning empty: %s", e)
        _presets_cache = None
        return []
    if not isinstance(data, list):
        _presets_cache = (PRESETS_PATH, mtime_ns, [])
        return []
    presets = [preset for p in data if (preset := _preset_from_json(p)) is not None]
    _presets_cache = (PRESETS_PATH, mtime_ns, list(presets))
    return _copy_presets(presets)


def _preset_from_json(raw: Any) -> Preset | None:
    """Parse one preset entry, returning None for malformed records."""
    if not isinstance(raw, dict):
        logger.warning("Skipping non-dict preset entry: %r", raw)
        return None
    raw_id = raw.get("id")
    raw_name = raw.get("name")
    if raw_id is None or raw_name is None:
        logger.warning("Skipping preset entry missing id/name: %s", raw)
        return None
    try:
        return Preset(id=str(raw_id), name=str(raw_name), config=raw.get("config"))
    except (TypeError, ValueError):
        logger.warning("Skipping malformed preset entry: %s", raw)
        return None


def _copy_presets(presets: list[Preset]) -> list[Preset]:
    """Per-instance copies so callers mutating a returned Preset can't
    corrupt the module cache. config dict is shallow-copied if present."""
    return [
        Preset(
            id=p.id,
            name=p.name,
            config=dict(p.config) if isinstance(p.config, dict) else p.config,
        )
        for p in presets
    ]


def _save_presets(presets: list[Preset]) -> None:
    global _presets_cache
    _atomic_write(
        PRESETS_PATH,
        json.dumps([asdict(p) for p in presets], indent=2),
    )
    _presets_cache = None


def upsert_preset(name: str, config: HpcAppConfig) -> list[Preset]:
    """Save a preset, replacing any existing one with the same name.

    Preserves the UUID of a replaced preset so a select binding on the
    preset id stays valid after the overwrite.
    """
    presets = load_presets()
    stripped = _preset_config(config)
    for i, p in enumerate(presets):
        if p.name == name:
            presets[i] = Preset(id=p.id, name=name, config=stripped)
            _save_presets(presets)
            return presets
    presets.append(Preset(id=str(uuid.uuid4()), name=name, config=stripped))
    _save_presets(presets)
    return presets


def _preset_config(config: HpcAppConfig) -> dict[str, Any]:
    """Serialize a config snapshot for preset persistence."""
    return strip_secrets_from_config(asdict(config))


def load_preset(preset_id: str) -> HpcAppConfig | None:
    """Load a preset by ID, or return None if not found."""
    for p in load_presets():
        if p.id == preset_id:
            if not isinstance(p.config, dict):
                logger.warning("Preset %s has no valid config", preset_id)
                return None
            return HpcAppConfig(**_filter_dataclass_fields(p.config, HpcAppConfig))
    return None


def delete_preset(preset_id: str) -> list[Preset]:
    """Delete a preset by ID and return the updated presets list."""
    presets = [p for p in load_presets() if p.id != preset_id]
    _save_presets(presets)
    return presets


# ---- Helpers ----


def strip_secrets_from_config(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive keys from a config dict."""
    return {k: v for k, v in config_dict.items() if k not in SENSITIVE_FIELDS}
