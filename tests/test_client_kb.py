from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from openkb.client.kb import (
    PathSecurityError,
    build_wiki_tree,
    delete_source_document_data,
    export_config_data,
    get_config_data,
    get_document_data,
    get_model_pool_data,
    get_source_document_data,
    get_status_data,
    import_config_data,
    init_kb,
    read_wiki_file,
    save_model_pool_profile,
    update_config_data,
    write_wiki_file,
)
from openkb.pageindex_local.runtime import read_pageindex_local_manifest


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
    (kb_dir / "wiki" / "concepts" / "retrieval.md").write_text(
        "---\nsources: [summaries/paper.md]\n---\n\n# Retrieval\n",
        encoding="utf-8",
    )

    data = get_document_data(kb_dir)

    assert data["documents"] == [
        {
            "hash": "hash-a",
            "name": "paper.pdf",
            "type": "short",
            "pages": 12,
            "stem": "paper",
            "raw_path": "raw/paper.pdf",
            "raw_exists": True,
            "source_path": None,
            "source_summary": "summaries/paper.md",
            "summary_exists": True,
            "related_count": 2,
            "related_pages": {
                "summaries": [
                    {"path": "summaries/paper.md", "page": "summaries/paper", "title": "paper", "shared": False}
                ],
                "companies": [],
                "industries": [],
                "themes": [],
                "metrics": [],
                "risks": [],
                "concepts": [
                    {"path": "concepts/retrieval.md", "page": "concepts/retrieval", "title": "retrieval", "shared": False}
                ],
            },
        },
        {
            "hash": "hash-b",
            "name": "manual.pdf",
            "type": "pageindex",
            "pages": 80,
            "stem": "manual",
            "raw_path": "raw/manual.pdf",
            "raw_exists": False,
            "source_path": None,
            "source_summary": "summaries/manual.md",
            "summary_exists": False,
            "related_count": 0,
            "related_pages": {
                "summaries": [],
                "companies": [],
                "industries": [],
                "themes": [],
                "metrics": [],
                "risks": [],
                "concepts": [],
            },
        },
    ]
    assert data["summaries"] == ["paper"]
    assert data["concepts"] == ["retrieval"]
    assert data["reports"] == ["lint.md"]


def test_source_document_data_and_delete_are_shared_with_client(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "companies").mkdir()
    (kb_dir / "wiki" / "sources" / "paper.md").write_text("# Full", encoding="utf-8")
    (kb_dir / "wiki" / "companies" / "TSMC.md").write_text(
        "---\nsources: [summaries/paper.md]\n---\n\n# TSMC",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "retrieval.md").write_text(
        "---\nsources: [summaries/paper.md, summaries/manual.md]\n---\n\n"
        "# Retrieval\n\n## Related Documents\n- [[summaries/paper]]\n- [[summaries/manual]]\n",
        encoding="utf-8",
    )

    detail = get_source_document_data(kb_dir, "paper")
    assert detail["name"] == "paper.pdf"
    assert detail["related_count"] == 3
    assert detail["related_pages"]["companies"][0]["path"] == "companies/TSMC.md"
    assert detail["related_pages"]["concepts"][0]["shared"] is True

    result = delete_source_document_data(kb_dir, "paper")

    assert result["removed_pages"] == ["summaries/paper.md", "companies/TSMC.md"]
    assert result["updated_pages"] == ["concepts/retrieval.md"]
    assert not (kb_dir / "wiki" / "companies" / "TSMC.md").exists()
    retrieval = (kb_dir / "wiki" / "concepts" / "retrieval.md").read_text(encoding="utf-8")
    assert "summaries/paper.md" not in retrieval
    assert "[[summaries/paper]]" not in retrieval


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
    (kb_dir / ".env").write_text("LLM_API_KEY=secret\nPADDLEOCR_TOKEN=paddle-secret\n", encoding="utf-8")

    config_data = get_config_data(kb_dir)
    assert config_data == {
        "model": "gpt-5.4-mini",
        "language": "zh",
        "pageindex_threshold": 20,
        "compile_max_concurrency": 2,
        "ocr_enabled": True,
        "ocr_detection_mode": "auto_recommend",
        "ocr_default_model": "PaddleOCR-VL-1.5",
        "ocr_chunk_pages": 100,
        "ocr_auto_recommend": True,
        "paddleocr_token": "paddle-secret",
        "pageindex_local_enabled": False,
        "pageindex_local_model": "",
        "pageindex_local_installation_state": "not_installed",
        "pageindex_local_repo_dir": "",
        "pageindex_local_python_path": "",
        "pageindex_local_script_path": "",
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
                "enabled": True,
                "tags": [],
                "features": [],
                "probe_models": ["gpt-5.4-mini"],
                "models": [{"name": "gpt-5.4-mini", "weight": 100}],
                "priority": 50,
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
            "ocr_enabled": False,
            "ocr_detection_mode": "always_ask",
            "ocr_default_model": "PP-StructureV3",
            "ocr_chunk_pages": 80,
            "ocr_auto_recommend": False,
            "paddleocr_token": "new-paddle-secret",
            "pageindex_local_enabled": True,
            "pageindex_local_model": "anthropic/claude-sonnet-4-6",
            "pageindex_local_installation_state": "installed",
            "pageindex_local_repo_dir": str(kb_dir / "runtime" / "repo"),
            "pageindex_local_python_path": str(kb_dir / "runtime" / "python.exe"),
            "pageindex_local_script_path": str(kb_dir / "runtime" / "run_pageindex.py"),
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "new-secret",
        },
    )

    assert updated["model"] == "anthropic/claude-sonnet-4-6"
    assert updated["base_url"] == "https://gateway.example.com/v1"
    assert updated["api_key"] == "new-secret"
    assert updated["ocr_enabled"] is False
    assert updated["ocr_detection_mode"] == "always_ask"
    assert updated["ocr_default_model"] == "PP-StructureV3"
    assert updated["ocr_chunk_pages"] == 80
    assert updated["ocr_auto_recommend"] is False
    assert updated["paddleocr_token"] == "new-paddle-secret"
    assert updated["pageindex_local_enabled"] is True
    assert updated["pageindex_local_model"] == "anthropic/claude-sonnet-4-6"
    assert updated["pageindex_local_installation_state"] == "installed"
    assert updated["pageindex_local_repo_dir"] == str(kb_dir / "runtime" / "repo")
    assert updated["pageindex_local_python_path"] == str(kb_dir / "runtime" / "python.exe")
    assert updated["pageindex_local_script_path"] == str(kb_dir / "runtime" / "run_pageindex.py")
    assert updated["profiles"][0]["model"] == "anthropic/claude-sonnet-4-6"
    assert updated["profiles"][0]["api_key"] == "new-secret"
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=new-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=new-secret\n" in env_text
    assert "PADDLEOCR_TOKEN=new-paddle-secret\n" in env_text
    runtime_manifest = read_pageindex_local_manifest(kb_dir / ".openkb" / "pageindex-local")
    assert runtime_manifest == {
        "repo_dir": str(kb_dir / "runtime" / "repo"),
        "python_path": str(kb_dir / "runtime" / "python.exe"),
        "script_path": str(kb_dir / "runtime" / "run_pageindex.py"),
    }
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["pageindex_threshold"] == 30
    assert saved["compile_max_concurrency"] == 4
    assert saved["ocr_enabled"] is False
    assert saved["ocr_detection_mode"] == "always_ask"
    assert saved["ocr_default_model"] == "PP-StructureV3"
    assert saved["ocr_chunk_pages"] == 80
    assert saved["ocr_auto_recommend"] is False
    assert saved["pageindex_local_enabled"] is True
    assert saved["pageindex_local_model"] == "anthropic/claude-sonnet-4-6"
    assert saved["pageindex_local_installation_state"] == "installed"
    assert saved["base_url"] == "https://gateway.example.com/v1"
    assert saved["active_llm_profile"] == "default"
    assert saved["llm_profiles"][0]["id"] == "default"
    assert saved["model_pool"]["enabled"] is True


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
    runtime_dir = source / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "repo").mkdir()
    (runtime_dir / "python.exe").write_text("", encoding="utf-8")
    (runtime_dir / "run_pageindex.py").write_text("", encoding="utf-8")
    update_config_data(
        source,
        {
            "create_profile": True,
            "profile_name": "Gateway",
            "model": "openai/doubao-seed-2-0-pro-260215",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "compile_max_concurrency": 3,
            "ocr_enabled": False,
            "ocr_detection_mode": "always_ask",
            "ocr_default_model": "PP-StructureV3",
            "ocr_chunk_pages": 60,
            "ocr_auto_recommend": False,
            "paddleocr_token": "paddle-secret",
            "pageindex_local_enabled": True,
            "pageindex_local_model": "gpt-5.4",
            "pageindex_local_installation_state": "installed",
            "pageindex_local_repo_dir": str(runtime_dir / "repo"),
            "pageindex_local_python_path": str(runtime_dir / "python.exe"),
            "pageindex_local_script_path": str(runtime_dir / "run_pageindex.py"),
            "model_pool_enabled": False,
            "model_pool_probe_interval_seconds": 300,
            "model_pool_failure_threshold": 2,
            "model_pool_timeout_seconds": 18,
            "api_key": "gateway-secret",
        },
    )
    save_model_pool_profile(
        source,
        {
            "name": "Gateway",
            "wire_api": "chat_completions",
            "base_url": "https://gateway.example.com/v1",
            "models": [
                {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
                {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
            ],
            "enabled": False,
        },
        profile_id="gateway",
    )

    exported = export_config_data(source)

    assert exported["format"] == "openkb.settings-config.v1"
    assert exported["active_profile"] == "gateway"
    assert exported["settings"]["compile_max_concurrency"] == 3
    assert exported["settings"]["ocr_enabled"] is False
    assert exported["settings"]["ocr_detection_mode"] == "always_ask"
    assert exported["settings"]["ocr_default_model"] == "PP-StructureV3"
    assert exported["settings"]["ocr_chunk_pages"] == 60
    assert exported["settings"]["ocr_auto_recommend"] is False
    assert exported["settings"]["paddleocr_token"] == "paddle-secret"
    assert exported["settings"]["pageindex_local_enabled"] is True
    assert exported["settings"]["pageindex_local_model"] == "gpt-5.4"
    assert exported["settings"]["pageindex_local_installation_state"] == "installed"
    assert exported["settings"]["pageindex_local_repo_dir"] == str(runtime_dir / "repo")
    assert exported["settings"]["pageindex_local_python_path"] == str(runtime_dir / "python.exe")
    assert exported["settings"]["pageindex_local_script_path"] == str(runtime_dir / "run_pageindex.py")
    assert exported["settings"]["model_pool_enabled"] is False
    assert exported["settings"]["model_pool_strategy"] == "weighted_round_robin"
    assert exported["settings"]["model_pool_probe_interval_seconds"] == 300
    assert exported["settings"]["model_pool_failure_threshold"] == 2
    assert exported["settings"]["model_pool_timeout_seconds"] == 18
    assert [profile["id"] for profile in exported["profiles"]] == ["default", "gateway"]
    assert exported["profiles"][0]["api_key"] == "default-secret"
    assert exported["profiles"][1]["api_key"] == "gateway-secret"
    assert exported["profiles"][1]["enabled"] is False
    assert exported["profiles"][1]["probe_models"] == [
        "openai/doubao-seed-2-0-pro-260215",
        "openai/doubao-seed-2-0-thinking",
    ]
    assert exported["profiles"][1]["models"] == [
        {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
        {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
    ]

    target = _make_kb(tmp_path / "target")
    (target / ".env").write_text("LLM_API_KEY=target-secret\n", encoding="utf-8")
    imported = import_config_data(target, exported)

    assert imported["active_profile"] == "gateway"
    assert imported["model"] == "openai/doubao-seed-2-0-pro-260215"
    assert imported["api_key"] == "gateway-secret"
    assert imported["compile_max_concurrency"] == 3
    assert imported["ocr_enabled"] is False
    assert imported["ocr_detection_mode"] == "always_ask"
    assert imported["ocr_default_model"] == "PP-StructureV3"
    assert imported["ocr_chunk_pages"] == 60
    assert imported["ocr_auto_recommend"] is False
    assert imported["paddleocr_token"] == "paddle-secret"
    assert imported["pageindex_local_enabled"] is True
    assert imported["pageindex_local_model"] == "gpt-5.4"
    assert imported["pageindex_local_installation_state"] == "installed"
    assert imported["pageindex_local_repo_dir"] == str(runtime_dir / "repo")
    assert imported["pageindex_local_python_path"] == str(runtime_dir / "python.exe")
    assert imported["pageindex_local_script_path"] == str(runtime_dir / "run_pageindex.py")
    assert [profile["id"] for profile in imported["profiles"]] == ["default", "gateway"]
    assert imported["profiles"][0]["api_key"] == "default-secret"
    assert imported["profiles"][1]["api_key"] == "gateway-secret"
    assert imported["profiles"][1]["enabled"] is False
    assert imported["profiles"][1]["models"] == [
        {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
        {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
    ]
    saved = yaml.safe_load((target / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["llm_profiles"][1]["api_key_env"] == "OPENKB_LLM_PROFILE_GATEWAY_API_KEY"
    assert saved["llm_profiles"][1]["enabled"] is False
    assert saved["llm_profiles"][1]["models"] == [
        {"name": "openai/doubao-seed-2-0-pro-260215", "weight": 3},
        {"name": "openai/doubao-seed-2-0-thinking", "weight": 1},
    ]
    assert saved["ocr_enabled"] is False
    assert saved["ocr_detection_mode"] == "always_ask"
    assert saved["ocr_default_model"] == "PP-StructureV3"
    assert saved["ocr_chunk_pages"] == 60
    assert saved["ocr_auto_recommend"] is False
    assert saved["pageindex_local_enabled"] is True
    assert saved["pageindex_local_model"] == "gpt-5.4"
    assert saved["pageindex_local_installation_state"] == "installed"
    assert saved["model_pool"] == {
        "enabled": False,
        "failure_threshold": 2,
        "probe_interval_seconds": 300,
        "strategy": "weighted_round_robin",
        "timeout_seconds": 18,
    }
    env_text = (target / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=gateway-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=default-secret\n" in env_text
    assert "OPENKB_LLM_PROFILE_GATEWAY_API_KEY=gateway-secret\n" in env_text
    assert "PADDLEOCR_TOKEN=paddle-secret\n" in env_text
    assert read_pageindex_local_manifest(target / ".openkb" / "pageindex-local") == {
        "repo_dir": str(runtime_dir / "repo"),
        "python_path": str(runtime_dir / "python.exe"),
        "script_path": str(runtime_dir / "run_pageindex.py"),
    }


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
    assert saved["model_pool"]["enabled"] is True
    assert saved["model_pool"]["strategy"] == "weighted_round_robin"
    assert saved["compile_max_concurrency"] == 3
    assert saved["ocr_enabled"] is True
    assert saved["ocr_default_model"] == "PaddleOCR-VL-1.5"
    assert saved["ocr_chunk_pages"] == 100
    assert saved["pageindex_local_enabled"] is False
    assert (kb_dir / ".openkb" / "hashes.json").read_text(encoding="utf-8") == "{}"
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-test\n" in env_text
    assert "OPENKB_LLM_PROFILE_DEFAULT_API_KEY=sk-test\n" in env_text


def test_model_pool_enabled_can_be_toggled_via_config_update(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)

    initial_pool = get_model_pool_data(kb_dir)
    assert initial_pool["enabled"] is True
    assert initial_pool["strategy"] == "weighted_round_robin"

    update_config_data(
        kb_dir,
        {
            "model_pool_enabled": False,
        },
    )

    disabled_pool = get_model_pool_data(kb_dir)
    assert disabled_pool["enabled"] is False

    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert saved["model_pool"]["enabled"] is False

    update_config_data(
        kb_dir,
        {
            "model_pool_enabled": True,
        },
    )

    enabled_pool = get_model_pool_data(kb_dir)
    assert enabled_pool["enabled"] is True
