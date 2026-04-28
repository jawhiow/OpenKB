from __future__ import annotations

import importlib.util
import json
import sys
import builtins
from pathlib import Path
from types import SimpleNamespace

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "agent-skills" / "openkb-agent-native"
SCRIPTS_DIR = SKILL_ROOT / "scripts"


def load_script_module(module_name: str):
    script_path = SCRIPTS_DIR / f"{module_name}.py"
    assert script_path.exists(), f"missing script: {script_path}"

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def block_openkb_imports(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "openkb" or name.startswith("openkb."):
            raise ModuleNotFoundError(f"blocked import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    for key in list(sys.modules):
        if key == "openkb" or key.startswith("openkb."):
            sys.modules.pop(key, None)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def make_minimal_kb(kb_dir: Path) -> None:
    (kb_dir / "raw").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "explorations").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True, exist_ok=True)
    (kb_dir / ".openkb").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "AGENTS.md").write_text("# Wiki Schema\n", encoding="utf-8")
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")
    (kb_dir / ".openkb" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "agent-native",
                "language": "zh",
                "pageindex_threshold": 20,
                "agent_native": True,
            },
            sort_keys=True,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (kb_dir / ".openkb" / "hashes.json").write_text("{}", encoding="utf-8")


def test_init_kb_creates_compatible_structure(tmp_path):
    module = load_script_module("init_kb")

    kb_dir = tmp_path / "fresh-kb"
    module.create_kb(kb_dir, language="zh")

    assert (kb_dir / ".openkb" / "config.yaml").exists()
    assert (kb_dir / ".openkb" / "hashes.json").exists()
    assert (kb_dir / "raw").is_dir()
    assert (kb_dir / "wiki" / "sources").is_dir()
    assert (kb_dir / "wiki" / "summaries").is_dir()
    assert (kb_dir / "wiki" / "concepts").is_dir()
    assert (kb_dir / "wiki" / "explorations").is_dir()
    assert (kb_dir / "wiki" / "reports").is_dir()
    assert (kb_dir / "wiki" / "AGENTS.md").exists()
    assert (kb_dir / "wiki" / "index.md").exists()
    assert (kb_dir / "wiki" / "log.md").exists()

    config = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"] == "agent-native"
    assert config["language"] == "zh"
    assert config["agent_native"] is True


def test_hash_registry_round_trip_and_hash_file(tmp_path):
    module = load_script_module("hash_registry")

    registry = module.HashRegistry(tmp_path / "hashes.json")
    sample = tmp_path / "sample.md"
    sample.write_text("hello skill\n", encoding="utf-8")
    digest = registry.hash_file(sample)

    registry.add(digest, {"name": sample.name, "type": "md"})

    assert registry.is_known(digest) is True
    assert registry.get(digest) == {"name": sample.name, "type": "md"}
    assert len(digest) == 64


def test_rebuild_index_collects_documents_concepts_and_explorations(tmp_path):
    module = load_script_module("rebuild_index")
    make_minimal_kb(tmp_path)

    (tmp_path / "wiki" / "summaries" / "doc-a.md").write_text(
        "---\ndoc_type: short\n---\n\n# 文档A\n\n这是文档 A 的摘要。\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "concepts" / "概念A.md").write_text(
        "---\nbrief: 这是概念 A 的一句话简介。\n---\n\n# 概念A\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "explorations" / "query-a.md").write_text(
        "# Query A\n\n这是一次保存的查询结果。\n",
        encoding="utf-8",
    )

    output = module.rebuild_index(tmp_path)

    assert "[[summaries/doc-a]]" in output
    assert "[[concepts/概念A]]" in output
    assert "[[explorations/query-a]]" in output


def test_status_reports_counts_and_total_indexed(tmp_path):
    module = load_script_module("status")
    make_minimal_kb(tmp_path)

    (tmp_path / "wiki" / "sources" / "doc-a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "wiki" / "summaries" / "doc-a.md").write_text("# Sum\n", encoding="utf-8")
    (tmp_path / "wiki" / "concepts" / "概念A.md").write_text("# Concept\n", encoding="utf-8")
    (tmp_path / ".openkb" / "hashes.json").write_text(
        json.dumps({"h1": {"name": "doc-a.md", "type": "md"}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    status = module.collect_status(tmp_path)

    assert status["directories"]["sources"] == 1
    assert status["directories"]["summaries"] == 1
    assert status["directories"]["concepts"] == 1
    assert status["total_indexed"] == 1


def test_lint_structural_reports_broken_wikilink(tmp_path):
    module = load_script_module("lint_structural")
    make_minimal_kb(tmp_path)

    (tmp_path / "wiki" / "concepts" / "概念A.md").write_text(
        "# 概念A\n\n参见 [[concepts/不存在的概念]]。\n",
        encoding="utf-8",
    )

    report = module.run_structural_lint(tmp_path)

    assert "不存在的概念" in report
    assert "broken" in report.lower()


def test_chat_store_round_trip(tmp_path):
    module = load_script_module("chat_store")
    make_minimal_kb(tmp_path)

    session = module.ChatSession.new(tmp_path, title="测试会话")
    session.record_turn("你好", "你好，我来帮你维护知识库。")

    loaded = module.load_session(tmp_path, session.id)
    sessions = module.list_sessions(tmp_path)

    assert loaded.user_turns == ["你好"]
    assert loaded.assistant_texts == ["你好，我来帮你维护知识库。"]
    assert sessions[0]["id"] == session.id

    assert module.delete_session(tmp_path, session.id) is True


def test_sync_raw_detects_new_and_changed_files(tmp_path):
    module = load_script_module("sync_raw")
    make_minimal_kb(tmp_path)

    raw_file = tmp_path / "raw" / "note.md"
    raw_file.write_text("first version\n", encoding="utf-8")

    pending = module.scan_pending(tmp_path)
    assert pending[0]["path"].endswith("note.md")
    assert pending[0]["reason"] == "new"

    registry = load_script_module("hash_registry").HashRegistry(tmp_path / ".openkb" / "hashes.json")
    registry.add(
        registry.hash_file(raw_file),
        {"name": "note.md", "type": "md", "raw_path": "raw/note.md"},
    )
    assert module.scan_pending(tmp_path) == []

    raw_file.write_text("second version\n", encoding="utf-8")
    changed = module.scan_pending(tmp_path)
    assert changed[0]["reason"] == "changed"


def test_convert_source_copies_markdown_to_raw_and_sources(tmp_path):
    module = load_script_module("convert_source")
    make_minimal_kb(tmp_path)

    source = tmp_path / "outside.md"
    source.write_text("# 外部文档\n\n这里是内容。\n", encoding="utf-8")

    result = module.convert_source_file(source, tmp_path)

    assert (tmp_path / "raw" / "outside.md").exists()
    assert (tmp_path / "wiki" / "sources" / "outside.md").exists()
    assert result["source_path"].endswith("wiki/sources/outside.md")


def test_convert_source_falls_back_to_local_pdf_markdown_for_long_pdf(tmp_path):
    module = load_script_module("convert_source")
    make_minimal_kb(tmp_path)

    pdf_path = tmp_path / "report.pdf"
    raw_pdf = tmp_path / "raw" / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    raw_pdf.write_bytes(b"%PDF-1.4 fake")

    module.convert_document = lambda _source, _kb: SimpleNamespace(
        raw_path=raw_pdf,
        source_path=None,
        is_long_doc=True,
        skipped=False,
        file_hash="fake-hash",
    )
    module.convert_pdf_with_images = lambda _pdf, _doc_name, _images_dir: "# Long PDF\n\nConverted locally.\n"

    result = module.convert_source_file(pdf_path, tmp_path)

    assert (tmp_path / "wiki" / "sources" / "report.md").exists()
    assert result["source_path"].endswith("wiki/sources/report.md")


def test_common_loads_without_openkb_dependency(monkeypatch):
    block_openkb_imports(monkeypatch)
    module = load_script_module("_common")
    assert "pageindex_threshold" in module.DEFAULT_CONFIG


def test_convert_source_loads_without_openkb_dependency(monkeypatch):
    block_openkb_imports(monkeypatch)
    module = load_script_module("convert_source")
    assert hasattr(module, "convert_source_file")
