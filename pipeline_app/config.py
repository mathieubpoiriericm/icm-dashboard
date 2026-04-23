"""Configuration dataclasses, persistence, and .env loading."""

from __future__ import annotations

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

from pipeline.config import LLM_PROVIDERS as _PIPELINE_LLM_PROVIDERS

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.json"
PRESETS_PATH = CONFIG_DIR / "presets.json"
TUNING_CONFIG_PATH = CONFIG_DIR / "tuning_config.json"
MAX_HISTORY: int = 100

SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "anthropic_api_key",
        "db_host",
        "db_port",
        "db_name",
        "db_user",
        "db_password",
        "ncbi_api_key",
        "entrez_email",
        "unpaywall_email",
    }
)

LLM_MODELS: list[str] = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]
LLM_EFFORTS: list[str] = ["low", "medium", "high", "max"]
LLM_PROVIDERS: list[str] = list(_PIPELINE_LLM_PROVIDERS)
PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic (Claude)",
    "ollama": "Ollama (local)",
}
PROMPT_VERSIONS: list[str] = ["v2", "v3", "v4", "v5", "ollama_v1"]


# ---- Dataclasses ----


@dataclass
class PipelineAppConfig:
    """Non-sensitive pipeline settings, persisted to config.json."""

    python_path: str = "python3"
    project_root: str = ""
    run_mode: str = "standard"
    days_back: int = 7
    dry_run: bool = False
    test_mode: bool = False
    sync_external_data: bool = False
    local_pdfs_path: str = ""
    pmids_path: str = ""
    skip_validation: bool = False
    llm_model: str = "claude-opus-4-7"
    llm_effort: str = "high"
    llm_max_tokens: int = 0
    prompt_version: str = "v5"
    llm_provider: str = "anthropic"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_num_ctx: int = 65_536
    confidence_threshold: float = 0.65
    max_concurrent_papers: int = 5
    rpm_limit: int = 50
    tpm_limit: int = 100_000
    estimated_tokens_per_call: int = 40_000
    ncbi_rate_limit: int = 10
    uniprot_rate_limit: int = 5
    max_paper_text_chars: int = 100_000
    max_retries: int = 1
    retry_delay: float = 2.0
    max_rate_limit_retries: int = 6
    rate_limit_retry_delay: float = 1.0
    max_connection_retries: int = 3
    connection_retry_delay: float = 2.0
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    db_command_timeout: float = 60.0
    progress_file: str = ""


@dataclass
class TuningConfig:
    """Tuning experiment settings, persisted to tuning_config.json."""

    pdf_path: str = ""
    gold_standard_path: str = "data/test_data/gold_standard/gold_standard_v2.csv"
    confidence_threshold: float = 0.7
    repeats: int = 1
    auto_advance: bool = False
    f_beta_weight: float = 2.0
    notes: str = ""
    use_main_config: bool = True
    llm_model: str = "claude-opus-4-7"
    llm_effort: str = "high"
    llm_max_tokens: int = 0
    prompt_version: str = "v5"
    llm_provider: str = "anthropic"
    ollama_model: str = "gemma4:e4b"
    ollama_host: str = "http://localhost:11434"
    ollama_num_ctx: int = 65_536
    python_path: str = "python3"
    project_root: str = ""


@dataclass
class EnvSecrets:
    """Credentials loaded from .env. Never persisted by the app."""

    anthropic_api_key: str = ""
    db_host: str = ""
    db_port: str = "5432"
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    ncbi_api_key: str = ""
    entrez_email: str = ""
    unpaywall_email: str = ""


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
    # dotenv returns strings verbatim; coerce non-numeric DB_PORT to the
    # default here so the pipeline subprocess gets a valid port string
    # instead of failing deep in asyncpg.
    raw_port = values.get("DB_PORT")
    if raw_port and not raw_port.isdigit():
        logger.warning(
            "Invalid DB_PORT=%r in .env; falling back to default 5432",
            raw_port,
        )
        raw_port = None
    secrets = EnvSecrets(
        anthropic_api_key=values.get("ANTHROPIC_API_KEY") or "",
        db_host=values.get("DB_HOST") or "",
        db_port=raw_port or "5432",
        db_name=values.get("DB_NAME") or "",
        db_user=values.get("DB_USER") or "",
        db_password=values.get("DB_PASSWORD") or "",
        ncbi_api_key=values.get("NCBI_API_KEY") or "",
        entrez_email=values.get("ENTREZ_EMAIL") or "",
        unpaywall_email=values.get("UNPAYWALL_EMAIL") or "",
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
        f = valid_fields[k]
        # Drop null JSON values; letting them through would overwrite the
        # dataclass default with None and cause a TypeError downstream
        # (e.g. int(None) on days_back).
        if v is None:
            logger.warning(
                "Field %s.%s dropped: got None (non-nullable)",
                cls.__name__,
                k,
            )
            continue
        # Resolve expected_type from the field's default, or from a sample
        # produced by default_factory when present. Without the factory
        # branch, list/dict fields would bypass the type-coercion block
        # entirely — a future list field with a hand-edited `"tags": "x"`
        # would reach the constructor as a string and crash at first iter.
        expected_type: type | None = None
        if f.default is not MISSING:
            expected_type = type(f.default)
        elif f.default_factory is not MISSING:
            try:
                expected_type = type(f.default_factory())
            except TypeError:
                expected_type = None
        if expected_type is not None:
            # bool is a subclass of int, so isinstance(True, int) is True —
            # without this guard, JSON `true`/`false` silently lands in an
            # int field as a bool value and flows through downstream typed
            # call sites unchanged.
            if expected_type is not bool and isinstance(v, bool):
                logger.warning(
                    "Field %s.%s=%r dropped (%s expected, got bool)",
                    cls.__name__,
                    k,
                    v,
                    expected_type.__name__,
                )
                continue
            if not isinstance(v, expected_type):
                # bool coercion is limited to int (json true/false decode to bool
                # already, so this only catches numeric 0/1). bool("False") is True
                # — never coerce arbitrary types.
                if expected_type is bool:
                    if isinstance(v, int):
                        v = bool(v)
                    else:
                        logger.warning(
                            "Field %s.%s=%r dropped (bool expected, got %s)",
                            cls.__name__,
                            k,
                            v,
                            type(v).__name__,
                        )
                        continue
                else:
                    try:
                        v = expected_type(v)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Field %s.%s=%r dropped (could not coerce to %s)",
                            cls.__name__,
                            k,
                            v,
                            expected_type.__name__,
                        )
                        continue
        result[k] = v
    return result


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


def _save_dataclass(path: Path, obj: Any) -> None:
    """Save a dataclass to JSON."""
    _atomic_write(path, json.dumps(asdict(obj), indent=2))


def load_config() -> PipelineAppConfig:
    """Load pipeline config from JSON, or return defaults."""
    return _load_dataclass(CONFIG_PATH, PipelineAppConfig)


def save_config(config: PipelineAppConfig) -> None:
    """Save pipeline config to JSON."""
    _save_dataclass(CONFIG_PATH, config)


def load_tuning_config() -> TuningConfig:
    """Load tuning config from JSON, or return defaults."""
    return _load_dataclass(TUNING_CONFIG_PATH, TuningConfig)


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
    return data


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
    presets = []
    for p in data:
        if not isinstance(p, dict):
            logger.warning("Skipping non-dict preset entry: %r", p)
            continue
        # Coerce id/name to str so a hand-edited "id": 42 doesn't become a
        # ghost preset that load_preset's string equality never matches.
        raw_id = p.get("id")
        raw_name = p.get("name")
        if raw_id is None or raw_name is None:
            logger.warning("Skipping preset entry missing id/name: %s", p)
            continue
        try:
            presets.append(
                Preset(
                    id=str(raw_id),
                    name=str(raw_name),
                    config=p.get("config"),
                )
            )
        except (TypeError, ValueError):
            logger.warning("Skipping malformed preset entry: %s", p)
    _presets_cache = (PRESETS_PATH, mtime_ns, list(presets))
    return _copy_presets(presets)


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


def save_preset(name: str, config: PipelineAppConfig) -> list[Preset]:
    """Save a new preset and return the updated presets list."""
    presets = load_presets()
    preset_id = str(uuid.uuid4())
    presets.append(
        Preset(
            id=preset_id,
            name=name,
            config=strip_secrets_from_config(asdict(config)),
        )
    )
    _save_presets(presets)
    return presets


def upsert_preset(name: str, config: PipelineAppConfig) -> list[Preset]:
    """Save a preset, replacing any existing one with the same name.

    Preserves the UUID of a replaced preset so a select binding on the
    preset id stays valid after the overwrite.
    """
    presets = load_presets()
    stripped = strip_secrets_from_config(asdict(config))
    for i, p in enumerate(presets):
        if p.name == name:
            presets[i] = Preset(id=p.id, name=name, config=stripped)
            _save_presets(presets)
            return presets
    presets.append(Preset(id=str(uuid.uuid4()), name=name, config=stripped))
    _save_presets(presets)
    return presets


def load_preset(preset_id: str) -> PipelineAppConfig | None:
    """Load a preset by ID, or return None if not found."""
    for p in load_presets():
        if p.id == preset_id:
            return PipelineAppConfig(
                **_filter_dataclass_fields(p.config, PipelineAppConfig)
            )
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
