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
PROMPT_VERSIONS: list[str] = ["v2", "v3", "v4", "v5"]


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


def load_env_secrets(project_root: str = "") -> EnvSecrets:
    """Read credentials from .env file in project root."""
    env_path = Path(project_root) / ".env" if project_root else Path(".env")
    values = dotenv_values(env_path) if env_path.exists() else {}
    return EnvSecrets(
        anthropic_api_key=values.get("ANTHROPIC_API_KEY") or "",
        db_host=values.get("DB_HOST") or "",
        db_port=values.get("DB_PORT") or "5432",
        db_name=values.get("DB_NAME") or "",
        db_user=values.get("DB_USER") or "",
        db_password=values.get("DB_PASSWORD") or "",
        ncbi_api_key=values.get("NCBI_API_KEY") or "",
        entrez_email=values.get("ENTREZ_EMAIL") or "",
        unpaywall_email=values.get("UNPAYWALL_EMAIL") or "",
    )


# ---- Config persistence ----


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def _filter_dataclass_fields(data: dict[str, Any], cls: type) -> dict[str, Any]:
    """Keep only keys that match dataclass field names, with type coercion."""
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
        if f.default is not MISSING:
            expected_type = type(f.default)
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
    """A named configuration snapshot."""

    id: str
    name: str
    config: dict[str, Any]


def load_presets() -> list[Preset]:
    """Load all presets from JSON."""
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read presets, returning empty: %s", e)
        return []
    if not isinstance(data, list):
        return []
    presets = []
    for p in data:
        try:
            presets.append(Preset(**p))
        except (TypeError, KeyError):
            logger.warning("Skipping malformed preset entry: %s", p)
    return presets


def _save_presets(presets: list[Preset]) -> None:
    _atomic_write(
        PRESETS_PATH,
        json.dumps([asdict(p) for p in presets], indent=2),
    )


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
