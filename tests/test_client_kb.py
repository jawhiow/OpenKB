from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from openkb.client.kb import (
    PathSecurityError,
    build_wiki_tree,
    export_config_data,
    get_config_data,
    get_document_data,
    get_status_data,
    import_config_data,
    init_kb,
    read_wiki_file,
    update_config_data,
    write_wiki_file,
)


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / "wiki" / "explorations").mkdir(parents=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True)
    (kb_dir / ".openkb").mkdir()
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4-mini\nlanguage: zh\npageindex_threshold: 20\nwire_api: responses\nbase_url: https://llm.example.com\n",
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "hash-a": {"name": "paper.pdf", "type": "pdf", "pages": 12},
                "hash-b": {"name": "manual.pdf", "type": "long_pdf", "pages": 80},
            }
        ),
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text("# Paper\n", encoding="utf-8")
    (kb_dir / "wiki" / "concepts" / "retrieval.md").write_text("# Retrieval\n", encoding="utf-8")
    (kb_dir / "wiki" / "reports" / "lint.md").write_text("# Lint\n", encoding="utf-8")
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF")
    return kb_dir


def test_get_status_data_returns_counts(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    status = get_status_data(kb_dir)

    assert status["kb_dir"] == str(kb_dir)
    assert status["directories"]["sources"] == 0
    assert status["directories"]["summaries"] == 1
    assert status["directories"]["concepts"] == 1
    assert status["directories"]["reports"] == 1
    assert status["directories"]["raw"] == 1
    assert status["total_indexed"] == 2


def test_get_document_data_maps_types_and_lists_wiki_pages(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    data = get_document_data(kb_dir)

    assert data["documents"] == [
        {"hash": "hash-a", "name": "paper.pdf", "type": "short", "pages": 12},
        {"hash": "hash-b", "name": "manual.pdf", "type": "pageindex", "pages": 80},
    ]
    assert data["summaries"] == ["paper"]
    assert data["concepts"] == ["retrieval"]
    assert data["reports"] == ["lint.md"]


def test_wiki_tree_and_file_access_are_limited_to_wiki_root(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    tree = build_wiki_tree(kb_dir)
    paths = [entry["path"] for entry in tree]

    assert paths == [
        "concepts/retrieval.md",
        "index.md",
        "reports/lint.md",
        "summaries/paper.md",
    ]
    summaries_entry = next(entry for entry in tree if entry["path"] == "summaries/paper.md")
    assert summaries_entry["directory"] == "summaries"
    assert summaries_entry["depth"] == 1
    assert summaries_entry["extension"] == ".md"
    assert read_wiki_file(kb_dir, "index.md")["content"] == "# Index\n"

    write_wiki_file(kb_dir, "concepts/new-page.md", "# New\n")
    assert (kb_dir / "wiki" / "concepts" / "new-page.md").read_text(encoding="utf-8") == "# New\n"

    with pytest.raises(PathSecurityError):
        read_wiki_file(kb_dir, "../.env")
    with pytest.raises(PathSecurityError):
        write_wiki_file(kb_dir, "../outside.md", "bad")


def test_config_data_can_be_updated_with_visible_api_key(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=secret\n", encoding="utf-8")

    config_data = get_config_data(kb_dir)
    assert config_data == {
        "model": "gpt-5.4-mini",
        "language": "zh",
        "pageindex_threshold": 20,
        "compile_max_concurrency": 2,
        "wire_api": "responses",
        "base_url": "https://llm.example.com",
        "api_key": "secret",
        "api_key_configured": True,
        "active_profile": "default",
        "profiles": [
            {
                "id": "default",
                "name": "Default",
                "model": "gpt-5.4-mini",
                "wire_api": "responses",
                "base_url": "https://llm.example.com",
                "api_key": "secret",
                "api_key_configured": True,
                "is_active": True,
            }
        ],
    }

    updated = update_config_data(
        kb_dir,
        {
            "model": "anthropic/claude-sonnet-4-6",
            "language": "en",
            "pageindex_threshold": 30,
            "compile_max_concurrency": 4,
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "new-secret",
        },
    )

    assert updated["model"] == "anthropic/claude-sonnet-4-6"
    assert updated["base_url"] == "https://gateway.example.com/v1"
    assert updated["api_key"] == "new-secret"
    assert updated["profiles"][0]["model"] == "anthropic/claude-sonnet-4-6"
    assert updated["profiles"][0]["api_key"] == "new-secret"
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=new-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=new-secret\n" in env_text
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["pageindex_threshold"] == 30
    assert saved["compile_max_concurrency"] == 4
    assert saved["base_url"] == "https://gateway.example.com/v1"
    assert saved["active_llm_profile"] == "default"
    assert saved["llm_profiles"][0]["id"] == "default"


def test_config_data_can_create_and_switch_llm_profiles_with_separate_keys(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / ".env").write_text("LLM_API_KEY=default-secret\n", encoding="utf-8")

    created = update_config_data(
        kb_dir,
        {
            "create_profile": True,
            "profile_name": "Claude Research",
            "model": "anthropic/claude-sonnet-4-6",
            "wire_api": "chat_completions",
            "base_url": "https://anthropic-gateway.example.com/v1",
            "api_key": "claude-secret",
        },
    )

    assert created["active_profile"] == "claude-research"
    assert created["model"] == "anthropic/claude-sonnet-4-6"
    assert [profile["id"] for profile in created["profiles"]] == ["default", "claude-research"]
    assert created["profiles"][0]["api_key"] == "default-secret"
    assert created["profiles"][1]["api_key"] == "claude-secret"
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=default-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_CLAUDE_RESEARCH_API_KEY=claude-secret\n" in env_text
    assert "LLM_API_KEY=claude-secret\n" in env_text

    switched = update_config_data(kb_dir, {"active_profile": "default"})

    assert switched["active_profile"] == "default"
    assert switched["model"] == "gpt-5.4-mini"
    assert switched["wire_api"] == "responses"
    assert switched["profiles"][0]["is_active"] is True
    assert switched["profiles"][1]["is_active"] is False
    assert switched["api_key"] == "default-secret"
    assert "LLM_API_KEY=default-secret\n" in (kb_dir / ".env").read_text(encoding="utf-8")


def test_config_profiles_can_be_exported_and_imported_with_api_keys(tmp_path: Path):
    source = _make_kb(tmp_path / "source")
    (source / ".env").write_text("LLM_API_KEY=default-secret\n", encoding="utf-8")
    update_config_data(
        source,
        {
            "create_profile": True,
            "profile_name": "Gateway",
            "model": "openai/doubao-seed-2-0-pro-260215",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "compile_max_concurrency": 3,
            "api_key": "gateway-secret",
        },
    )

    exported = export_config_data(source)

    assert exported["format"] == "openkb.llm-config.v1"
    assert exported["active_profile"] == "gateway"
    assert exported["settings"]["compile_max_concurrency"] == 3
    assert [profile["id"] for profile in exported["profiles"]] == ["default", "gateway"]
    assert exported["profiles"][0]["api_key"] == "default-secret"
    assert exported["profiles"][1]["api_key"] == "gateway-secret"

    target = _make_kb(tmp_path / "target")
    (target / ".env").write_text("LLM_API_KEY=target-secret\n", encoding="utf-8")
    imported = import_config_data(target, exported)

    assert imported["active_profile"] == "gateway"
    assert imported["model"] == "openai/doubao-seed-2-0-pro-260215"
    assert imported["api_key"] == "gateway-secret"
    assert imported["compile_max_concurrency"] == 3
    assert [profile["id"] for profile in imported["profiles"]] == ["default", "gateway"]
    assert imported["profiles"][0]["api_key"] == "default-secret"
    assert imported["profiles"][1]["api_key"] == "gateway-secret"
    saved = yaml.safe_load((target / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["llm_profiles"][1]["api_key_env"] == "OPENKB_LLM_PROFILE_GATEWAY_API_KEY"
    env_text = (target / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=gateway-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=default-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_GATEWAY_API_KEY=gateway-secret\n" in env_text


def test_init_kb_creates_openkb_layout(tmp_path: Path):
    kb_dir = tmp_path / "new-kb"

    result = init_kb(
        kb_dir,
        model="gpt-5.4-mini",
        language="zh",
        pageindex_threshold=12,
        compile_max_concurrency=3,
        wire_api="responses",
        base_url="https://gateway.example.com",
        api_key="sk-test",
    )

    assert result["kb_dir"] == str(kb_dir)
    assert (kb_dir / "raw").is_dir()
    assert (kb_dir / "wiki" / "AGENTS.md").is_file()
    assert (kb_dir / "wiki" / "index.md").is_file()
    assert (kb_dir / ".openkb" / "config.yaml").is_file()
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://gateway.example.com"
    assert saved["compile_max_concurrency"] == 3
    assert (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8") == "{}"
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-test\n" in env_text
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=sk-test\n" in env_text
