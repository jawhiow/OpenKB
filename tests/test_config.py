import pytest
from pathlib import Path
from openkb.config import DEFAULT_CONFIG, load_config, save_config


def test_default_config_keys():
    assert "model" in DEFAULT_CONFIG
    assert "language" in DEFAULT_CONFIG
    assert "pageindex_threshold" in DEFAULT_CONFIG
    assert "wire_api" in DEFAULT_CONFIG
    assert "ocr_enabled" in DEFAULT_CONFIG
    assert "ocr_default_model" in DEFAULT_CONFIG
    assert "ocr_chunk_pages" in DEFAULT_CONFIG
    assert "pageindex_local_enabled" in DEFAULT_CONFIG


def test_default_config_values():
    assert DEFAULT_CONFIG["model"] == "gpt-5.4-mini"
    assert DEFAULT_CONFIG["language"] == "en"
    assert DEFAULT_CONFIG["pageindex_threshold"] == 20
    assert DEFAULT_CONFIG["wire_api"] == "chat_completions"
    assert DEFAULT_CONFIG["ocr_enabled"] is True
    assert DEFAULT_CONFIG["ocr_default_model"] == "PaddleOCR-VL-1.5"
    assert DEFAULT_CONFIG["ocr_chunk_pages"] == 100
    assert DEFAULT_CONFIG["pageindex_local_enabled"] is False


def test_load_missing_file_returns_defaults(tmp_path):
    missing = tmp_path / "nonexistent" / "config.yaml"
    config = load_config(missing)
    assert config == DEFAULT_CONFIG


def test_save_creates_parent_dirs(tmp_path):
    config_path = tmp_path / "nested" / "dir" / "config.yaml"
    save_config(config_path, DEFAULT_CONFIG)
    assert config_path.exists()


def test_save_load_roundtrip(tmp_path):
    config_path = tmp_path / "config.yaml"
    custom = {"model": "gpt-3.5-turbo", "language": "fr"}
    save_config(config_path, custom)
    loaded = load_config(config_path)
    # Custom values override defaults
    assert loaded["model"] == "gpt-3.5-turbo"
    assert loaded["language"] == "fr"
    # Defaults fill in missing keys
    assert loaded["pageindex_threshold"] == DEFAULT_CONFIG["pageindex_threshold"]


def test_load_overrides_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    save_config(config_path, {"model": "claude-3", "pageindex_threshold": 100})
    loaded = load_config(config_path)
    assert loaded["model"] == "claude-3"
    assert loaded["pageindex_threshold"] == 100
    # Non-overridden defaults still present
    assert loaded["language"] == "en"
    assert loaded["wire_api"] == "chat_completions"
    assert loaded["ocr_chunk_pages"] == 100
