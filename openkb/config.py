from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "gpt-5.4-mini",
    "language": "en",
    "pageindex_threshold": 20,
    "compile_max_concurrency": 2,
    "ocr_enabled": True,
    "ocr_detection_mode": "auto_recommend",
    "ocr_default_model": "PaddleOCR-VL-1.5",
    "ocr_chunk_pages": 100,
    "ocr_auto_recommend": True,
    "pageindex_local_enabled": False,
    "pageindex_local_model": "",
    "pageindex_local_installation_state": "not_installed",
    "model_pool": {
        "enabled": True,
        "strategy": "weighted_round_robin",
        "probe_interval_seconds": 600,
        "failure_threshold": 3,
        "timeout_seconds": 12,
    },
    "wire_api": "chat_completions",
    "base_url": "",
}

GLOBAL_CONFIG_DIR = Path.home() / ".config" / "openkb"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "global.yaml"


def _merge_config_defaults(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Path) -> dict[str, Any]:
    """Load YAML config from config_path, merged with DEFAULT_CONFIG.

    If the file does not exist, returns a copy of the defaults.
    """
    config = deepcopy(DEFAULT_CONFIG)
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        config = _merge_config_defaults(config, data)
    return config


def save_config(config_path: Path, config: dict) -> None:
    """Persist config dict to YAML, creating parent directories as needed."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=True)


def load_global_config() -> dict[str, Any]:
    """Load the global config from ~/.config/openkb/global.yaml."""
    if GLOBAL_CONFIG_PATH.exists():
        with GLOBAL_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def save_global_config(config: dict[str, Any]) -> None:
    """Save the global config to ~/.config/openkb/global.yaml."""
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with GLOBAL_CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=True)


def register_kb(kb_path: Path) -> None:
    """Register a KB path in the global config's known_kbs list."""
    gc = load_global_config()
    known = gc.get("known_kbs", [])
    resolved = str(kb_path.resolve())
    if resolved not in known:
        known.append(resolved)
        gc["known_kbs"] = known
    gc["default_kb"] = resolved
    save_global_config(gc)
