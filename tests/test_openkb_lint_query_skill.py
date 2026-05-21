"""Tests for bundled openkb-lint-query skill scripts."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "agent-skills"
    / "openkb-lint-query"
    / "scripts"
)
SKILL_DIR = SCRIPTS_DIR.parent


def _load_script(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        f"openkb_lint_query_{name}",
        SCRIPTS_DIR / f"{name}.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_skill_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki" / "companies").mkdir(parents=True)
    (kb / "wiki" / "concepts").mkdir()
    (kb / "wiki" / "reports").mkdir()
    (kb / "wiki" / "summaries").mkdir()
    (kb / "wiki" / "index.md").write_text(
        "# Index\n\n## Documents\n\n## Companies\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    return kb


def test_draft_page_does_not_emit_todo_scaffolding():
    runtime = _load_script("_runtime")

    content = runtime.draft_page("AI CPU", "concepts", "Missing coverage.")

    assert "TODO" not in content
    assert "status: draft" in content
    assert "## Source Evidence" in content


def test_lint_kb_add_todos_flag_is_deprecated_noop(tmp_path):
    lint_kb = _load_script("lint_kb")
    kb = _make_skill_kb(tmp_path)
    page = kb / "wiki" / "companies" / "Tencent.md"
    page.write_text("# Tencent\n\nTencent is an operating company.", encoding="utf-8")

    result = lint_kb.build_lint(str(kb), apply_safe=True, add_todos=True)

    assert result["ok"] is True
    text = page.read_text(encoding="utf-8")
    assert "TODO" not in text
    assert "## Source Evidence" not in text
    assert not any(item["action"] == "append_source_evidence_todo" for item in result["fix_plan"])
    report = (kb / "wiki" / result["report"]).read_text(encoding="utf-8")
    assert "TODO" not in report


def test_lint_kb_uses_system_report_when_openkb_cli_is_available(tmp_path):
    lint_kb = _load_script("lint_kb")
    kb = _make_skill_kb(tmp_path)
    page = kb / "wiki" / "companies" / "Tencent.md"
    page.write_text("# Tencent\n\nTencent is an operating company.", encoding="utf-8")
    system_report = kb / "wiki" / "reports" / "system-lint.md"
    calls: list[tuple[Path, bool]] = []

    async def fake_run_lint(kb_root, fix=False):
        calls.append((Path(kb_root), fix))
        system_report.parent.mkdir(parents=True, exist_ok=True)
        system_report.write_text("# System Lint\n\nReport from openkb lint.", encoding="utf-8")
        return system_report

    async def fake_knowledge_lint(*_args, **_kwargs):
        return "## Semantic\n\nNo issues."

    with patch("openkb.cli.run_lint", new=fake_run_lint), \
        patch("openkb.agent.linter.run_knowledge_lint", new=fake_knowledge_lint), \
        patch.object(lint_kb, "report_timestamp", return_value="20260101_000000"):
        result = lint_kb.build_lint(str(kb), apply_safe=False)

    assert result["ok"] is True
    assert result["lint_backend"] == "system"
    assert calls == [(kb, False)]
    assert result["report"] == "reports/system-lint.md"
    assert system_report.read_text(encoding="utf-8").startswith("# System Lint")
    assert not (kb / "wiki" / "reports" / "lint_20260101_000000.md").exists()


def test_apply_fixes_ignores_deprecated_source_evidence_todo_action(tmp_path):
    apply_fixes = _load_script("apply_fixes")
    kb = _make_skill_kb(tmp_path)
    page = kb / "wiki" / "companies" / "Tencent.md"
    page.write_text("# Tencent\n\nTencent is an operating company.", encoding="utf-8")
    plan = kb / "wiki" / "reports" / "plan.json"
    plan.write_text(
        json.dumps({
            "fix_plan": [{
                "action": "append_source_evidence_todo",
                "path": "companies/Tencent.md",
                "approved": True,
            }],
        }),
        encoding="utf-8",
    )

    result = apply_fixes.apply_plan(str(kb), str(plan))

    assert result["applied"] == []
    assert result["skipped"][0]["skip_reason"] == "deprecated action"
    assert "TODO" not in page.read_text(encoding="utf-8")


def test_save_exploration_read_set_note_is_todo_free(tmp_path):
    save_exploration = _load_script("save_exploration")
    kb = _make_skill_kb(tmp_path)
    answer = tmp_path / "answer.md"
    answer.write_text("Answer with citations.", encoding="utf-8")

    result = save_exploration.save(str(kb), "AI Notes", str(answer))

    assert result["ok"] is True
    text = (kb / "wiki" / result["path"]).read_text(encoding="utf-8")
    assert "TODO" not in text
    assert "## Read Set" in text


def test_runtime_bootstrap_adds_sibling_openkb_checkout_venv_site_packages(tmp_path, monkeypatch):
    runtime = _load_script("_runtime")
    workspace = tmp_path / "workspace"
    kb = workspace / "kb"
    repo = workspace / "OpenKB"
    site_packages = repo / ".venv" / "lib" / "python3.10" / "site-packages"
    (repo / "openkb").mkdir(parents=True)
    site_packages.mkdir(parents=True)
    (repo / "openkb" / "cli.py").write_text("", encoding="utf-8")
    kb.mkdir(parents=True)

    original_sys_path = list(sys.path)
    try:
        monkeypatch.chdir(kb)
        detected = runtime.bootstrap_openkb_repo_path()

        assert detected == repo
        assert str(repo) in sys.path
        assert str(site_packages) in sys.path
        assert sys.path.index(str(repo)) < sys.path.index(str(site_packages))
    finally:
        sys.path[:] = original_sys_path


def test_query_context_detects_investment_decision_and_adds_method_anchors(tmp_path):
    query_context = _load_script("query_context")
    kb = _make_skill_kb(tmp_path)
    wiki = kb / "wiki"
    for rel in [
        "companies/Tencent.md",
        "summaries/tencent-annual.md",
        "concepts/价值投资.md",
        "concepts/安全边际.md",
        "concepts/内在价值.md",
        "concepts/企业护城河.md",
        "concepts/capital_allocation.md",
        "concepts/ROE与杜邦分析.md",
    ]:
        (wiki / rel).write_text(f"# {Path(rel).stem}\n", encoding="utf-8")

    query_context.search = lambda *_args, **_kwargs: {
        "results": [
            {"path": "companies/Tencent.md", "title": "Tencent", "snippet": ""},
            {"path": "summaries/tencent-annual.md", "title": "Tencent Annual", "snippet": ""},
        ],
    }

    data = query_context.build_context(str(kb), "腾讯2025年报可以投资吗？")

    assert data["query_type"] == "investment_decision"
    read_set = data["read_set_suggestion"]
    assert "companies/Tencent.md" in read_set
    assert "summaries/tencent-annual.md" in read_set
    assert "concepts/价值投资.md" in read_set
    assert "concepts/安全边际.md" in read_set
    assert "concepts/内在价值.md" in read_set
    assert "concepts/企业护城河.md" in read_set
    assert data["answer_contract"]["investment_decision_framework"] == [
        "key_financial_facts",
        "moat_and_business_quality",
        "cash_flow_and_capital_allocation",
        "contra_evidence_and_risks",
        "valuation_and_margin_of_safety",
        "decision_grade",
    ]
    assert "external real-time valuation data" in data["answer_contract"]["valuation_data_notice"]


def test_query_context_warns_when_investment_method_anchor_missing(tmp_path):
    query_context = _load_script("query_context")
    kb = _make_skill_kb(tmp_path)
    (kb / "wiki" / "companies" / "Tencent.md").write_text("# Tencent\n", encoding="utf-8")

    query_context.search = lambda *_args, **_kwargs: {
        "results": [{"path": "companies/Tencent.md", "title": "Tencent", "snippet": ""}],
    }

    data = query_context.build_context(str(kb), "腾讯估值是否合理，能不能买？")

    assert data["query_type"] == "investment_decision"
    assert "companies/Tencent.md" in data["read_set_suggestion"]
    assert not any(path.startswith("concepts/") for path in data["read_set_suggestion"])
    assert any("Missing investment method anchor page" in warning for warning in data["warnings"])


def test_skill_metadata_mentions_add_and_delete_workflows():
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    openai_text = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "add_documents.py" in skill_text
    assert "delete_source.py" in skill_text
    assert "kb_inventory.py" in skill_text
    assert "maintenance.py" in skill_text
    assert "import-only" in skill_text
    assert "auto-review" in skill_text
    assert "--promote" in skill_text
    assert "legacy-compile" in skill_text
    assert "summary_scorecard" in skill_text
    assert "backfill-ledger" in skill_text
    assert "merge-concepts" in skill_text
    assert "h1-rename" in skill_text
    assert "新增" in skill_text or "add" in skill_text.lower()
    assert "删除" in skill_text or "delete" in skill_text.lower()
    assert "Add" in openai_text or "add" in openai_text
    assert "staged" in openai_text
    assert "scored review summaries" in openai_text
    assert "do not promote" in openai_text
    assert "delete" in openai_text.lower()
    assert "maintain" in openai_text.lower()


def test_add_documents_rejects_unsupported_extension(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    source = tmp_path / "ignore.xyz"
    source.write_text("skip", encoding="utf-8")

    result = add_documents.add_documents(str(kb), str(source))

    assert result["ok"] is False
    assert "Unsupported file type" in result["error"]
    assert result["added"] == []


def test_add_documents_default_runs_staged_import_and_summary_without_promotion(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    first = docs / "a.md"
    second = docs / "b.txt"
    skipped = docs / "c.xyz"
    first.write_text("# A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    skipped.write_text("C", encoding="utf-8")

    import_calls: list[tuple[Path, Path, bool, str | None]] = []

    def fake_import_document_source(file_path, kb_root, *, force=False, strategy_override=None):
        import_calls.append((Path(file_path), Path(kb_root), force, strategy_override))
        return {
            "name": Path(file_path).name,
            "file_hash": f"hash-{Path(file_path).stem}",
            "skipped": False,
            "raw_path": f"raw/{Path(file_path).name}",
            "source_path": f"wiki/sources/{Path(file_path).stem}.md",
        }

    def fake_summarize_documents(kb_root, *, file_hashes=None, force=False, max_workers=1, progress_callback=None):
        return {
            "generated": len(file_hashes or []),
            "skipped": 0,
            "failed": 0,
            "total": len(file_hashes or []),
            "failures": [],
            "documents": [
                {
                    "file_hash": file_hash,
                    "name": f"{file_hash}.md",
                    "skipped": False,
                    "summary_path": f"review_summaries/{file_hash}.md",
                }
                for file_hash in file_hashes or []
            ],
        }

    with patch.object(add_documents, "add_single_file") as legacy_add, \
        patch.object(add_documents, "import_document_source", side_effect=fake_import_document_source), \
        patch.object(add_documents, "summarize_documents", side_effect=fake_summarize_documents) as summarize, \
        patch.object(add_documents, "promote_summary_documents") as promote, \
        patch.object(add_documents, "commit_kb_changes", return_value=None):
        result = add_documents.add_documents(str(kb), str(docs), force=True)

    assert result["ok"] is True
    assert result["workflow"] == "staged"
    assert result["added"] == []
    assert [item["file_hash"] for item in result["imported"]] == ["hash-a", "hash-b"]
    assert result["summary"]["generated"] == 2
    assert result["promotion"]["promoted"] == 0
    assert result["skipped_unsupported"] == [str(skipped)]
    assert import_calls == [
        (first, kb, True, None),
        (second, kb, True, None),
    ]
    summarize.assert_called_once_with(kb, file_hashes=["hash-a", "hash-b"], force=True, max_workers=1)
    legacy_add.assert_not_called()
    promote.assert_not_called()


def test_add_documents_scores_existing_inventory_file_when_import_pipeline_skips(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    source = tmp_path / "already.md"
    source.write_text("# Already", encoding="utf-8")

    with patch.object(add_documents, "add_single_file") as legacy_add, \
        patch.object(
            add_documents,
            "import_document_source",
            return_value={"name": "already.md", "file_hash": "hash-a", "skipped": True},
        ), \
        patch.object(
            add_documents,
            "summarize_documents",
            return_value={"generated": 1, "skipped": 0, "failed": 0, "total": 1, "failures": [], "documents": []},
        ) as summarize:
        result = add_documents.add_documents(str(kb), str(source))

    assert result["ok"] is True
    assert result["workflow"] == "staged"
    assert result["added"] == []
    assert result["skipped"] == [str(source)]
    assert result["file_hashes"] == ["hash-a"]
    assert result["summary"]["generated"] == 1
    legacy_add.assert_not_called()
    summarize.assert_called_once_with(kb, file_hashes=["hash-a"], force=False, max_workers=1)


def test_add_documents_runs_auto_review_and_promotion_only_when_requested(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    source = tmp_path / "paper.md"
    source.write_text("# Paper", encoding="utf-8")

    with patch.object(
        add_documents,
        "import_document_source",
        return_value={
            "name": "paper.md",
            "file_hash": "hash-paper",
            "skipped": False,
            "raw_path": "raw/paper.md",
            "source_path": "wiki/sources/paper.md",
        },
    ), \
        patch.object(
            add_documents,
            "summarize_documents",
            return_value={"generated": 1, "skipped": 0, "failed": 0, "total": 1, "failures": [], "documents": []},
        ), \
        patch.object(
            add_documents,
            "run_auto_review",
            return_value={
                "run_id": "auto_run_test",
                "dry_run": False,
                "total": 1,
                "approved": 1,
                "rejected": 0,
                "held_for_human": 0,
                "errors": 0,
                "decisions": [{"file_hash": "hash-paper", "final_decision": "approved"}],
            },
        ) as auto_review, \
        patch.object(
            add_documents,
            "promote_summary_documents",
            return_value={"promoted": 1, "skipped": 0, "failed": 0, "total": 1, "failures": [], "documents": []},
        ) as promote, \
        patch.object(add_documents, "commit_kb_changes", return_value=None):
        result = add_documents.add_documents(str(kb), str(source), auto_review=True, promote=True)

    assert result["ok"] is True
    assert result["workflow"] == "staged"
    assert result["auto_review"]["approved"] == 1
    assert result["promotion"]["promoted"] == 1
    auto_review.assert_called_once_with(kb, file_hashes=["hash-paper"], dry_run=False, operator="")
    promote.assert_called_once_with(kb, file_hashes=["hash-paper"], force=False, max_workers=1)


def test_add_documents_legacy_compile_remains_explicit(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    source = tmp_path / "paper.md"
    source.write_text("# Paper", encoding="utf-8")

    def fake_add_single_file(file_path, kb_dir, *, force=False, strict=False):
        openkb_dir = Path(kb_dir) / ".openkb"
        hashes_path = openkb_dir / "hashes.json"
        hashes = json.loads(hashes_path.read_text(encoding="utf-8")) if hashes_path.exists() else {}
        hashes["hash-paper"] = {"name": Path(file_path).name, "type": "md"}
        hashes_path.write_text(json.dumps(hashes), encoding="utf-8")

    with patch.object(add_documents, "add_single_file", side_effect=fake_add_single_file) as legacy_add, \
        patch.object(add_documents, "import_document_source") as import_source, \
        patch.object(add_documents, "summarize_documents") as summarize:
        result = add_documents.add_documents(str(kb), str(source), legacy_compile=True)

    assert result["ok"] is True
    assert result["workflow"] == "legacy_compile"
    assert result["added"] == [str(source)]
    legacy_add.assert_called_once()
    import_source.assert_not_called()
    summarize.assert_not_called()


def test_add_documents_validates_ingest_gate_force_reason(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    source = tmp_path / "paper.md"
    source.write_text("# Paper", encoding="utf-8")

    result = add_documents.add_documents(str(kb), str(source), force_gate_pass=True)

    assert result["ok"] is False
    assert "gate_reason" in result["error"]


def test_add_documents_import_only_uses_import_pipeline_and_logs(tmp_path):
    add_documents = _load_script("add_documents")
    kb = _make_skill_kb(tmp_path)
    source = tmp_path / "paper.md"
    source.write_text("# Paper", encoding="utf-8")
    calls: list[tuple[Path, Path, bool, str | None]] = []

    def fake_import_document_source(file_path, kb_root, *, force=False, strategy_override=None):
        calls.append((Path(file_path), Path(kb_root), force, strategy_override))
        return {
            "name": Path(file_path).name,
            "file_hash": "hash-paper",
            "skipped": False,
            "raw_path": "raw/paper.md",
            "source_path": "wiki/sources/paper.md",
        }

    with patch.object(add_documents, "import_document_source", side_effect=fake_import_document_source), \
        patch.object(add_documents, "commit_kb_changes", return_value=None):
        result = add_documents.add_documents(
            str(kb),
            str(source),
            import_only=True,
            force=True,
            strategy_override="ocr-pageindex-local",
        )

    assert result["ok"] is True
    assert result["import_only"] is True
    assert result["imported"][0]["file_hash"] == "hash-paper"
    assert calls == [(source, kb, True, "ocr-pageindex-local")]
    assert "import | 1 source document(s)" in (kb / "wiki" / "log.md").read_text(encoding="utf-8")


def test_kb_inventory_status_list_and_source_detail(tmp_path):
    kb_inventory = _load_script("kb_inventory")
    kb = _make_skill_kb(tmp_path)
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-a": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    (kb / "raw").mkdir()
    (kb / "raw" / "paper.pdf").write_bytes(b"%PDF")
    (kb / "wiki" / "summaries" / "paper.md").write_text("# Paper\n", encoding="utf-8")

    status = kb_inventory.status(str(kb))
    listing = kb_inventory.inventory(str(kb), include_pages=True, include_ledger=True)
    detail = kb_inventory.source_detail(str(kb), "paper")

    assert status["ok"] is True
    assert status["indexed_documents"] == 1
    assert status["counts"]["summaries"] == 1
    assert listing["ok"] is True
    assert listing["documents"][0]["name"] == "paper.pdf"
    assert listing["pages"]["summaries"][0]["path"] == "summaries/paper.md"
    assert "hash-a" in listing["ledger"]
    assert detail["ok"] is True
    assert detail["document"]["name"] == "paper.pdf"


def test_maintenance_rebuild_defaults_to_dry_run(tmp_path):
    maintenance = _load_script("maintenance")
    kb = _make_skill_kb(tmp_path)
    raw = kb / "raw"
    raw.mkdir()
    source = raw / "paper.md"
    source.write_text("# Paper", encoding="utf-8")

    result = maintenance.rebuild(str(kb))

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["would_rebuild"] == [str(source)]


def test_maintenance_rebuild_yes_noops_without_raw_documents(tmp_path):
    maintenance = _load_script("maintenance")
    kb = _make_skill_kb(tmp_path)

    with patch.object(maintenance, "staged_add_documents") as staged_add:
        result = maintenance.rebuild(str(kb), yes=True)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["workflow"] == "staged_rebuild"
    assert result["rebuilt"] == []
    assert result["failed"] == []
    staged_add.assert_not_called()


def test_maintenance_rebuild_uses_staged_add_without_promotion(tmp_path):
    maintenance = _load_script("maintenance")
    kb = _make_skill_kb(tmp_path)
    raw = kb / "raw"
    raw.mkdir()
    source = raw / "paper.md"
    source.write_text("# Paper", encoding="utf-8")
    calls: list[tuple[str, str, bool, bool]] = []

    def fake_staged_add_documents(kb_arg, path_arg, *, force=False, strict=False, promote=False, **_kwargs):
        calls.append((kb_arg, path_arg, force, promote))
        return {
            "ok": True,
            "workflow": "staged",
            "imported": [{"file_hash": "hash-paper", "name": "paper.md"}],
            "file_hashes": ["hash-paper"],
            "summary": {"generated": 1, "skipped": 0, "failed": 0, "total": 1, "failures": [], "documents": []},
            "promotion": {"promoted": 0, "skipped": 0, "failed": 0, "total": 0, "failures": [], "documents": []},
            "failed": [],
        }

    with patch.object(maintenance, "staged_add_documents", side_effect=fake_staged_add_documents):
        result = maintenance.rebuild(str(kb), yes=True, strict=True)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["workflow"] == "staged_rebuild"
    assert result["rebuilt"] == [str(source)]
    assert result["staged"]["summary"]["generated"] == 1
    assert result["staged"]["promotion"]["promoted"] == 0
    assert calls == [(str(kb), str(raw), True, False)]


def test_maintenance_merge_concepts_dry_run_uses_system_proposals(tmp_path):
    maintenance = _load_script("maintenance")
    kb = _make_skill_kb(tmp_path)

    class Proposal:
        canonical = "AI"
        merged = ["AI", "人工智能"]
        rationale = {"人工智能": 0.9}
        sources_union = ["summaries/a.md"]

    with patch.object(maintenance, "propose_merges", return_value=[Proposal()]):
        result = maintenance.merge_concepts(str(kb))

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["proposals"] == [{
        "canonical": "AI",
        "merged": ["AI", "人工智能"],
        "rationale": {"人工智能": 0.9},
        "sources_union": ["summaries/a.md"],
    }]


def test_maintenance_backfill_ledger_commits_when_changed(tmp_path):
    maintenance = _load_script("maintenance")
    kb = _make_skill_kb(tmp_path)

    with patch.object(maintenance, "backfill_document_ledger", return_value={"added": 1, "updated": 0, "unchanged": 0, "total": 1}), \
        patch.object(maintenance, "commit_kb_changes", return_value=None) as commit:
        result = maintenance.backfill_ledger(str(kb))

    assert result["ok"] is True
    assert result["result"]["added"] == 1
    commit.assert_called_once_with(kb, "Backfill document ledger")


def test_delete_source_defaults_to_dry_run_without_mutating(tmp_path):
    delete_source = _load_script("delete_source")
    kb = _make_skill_kb(tmp_path)
    raw = kb / "raw"
    raw.mkdir()
    (kb / ".openkb").mkdir(exist_ok=True)
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-a": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    (raw / "paper.pdf").write_bytes(b"%PDF")
    summary = kb / "wiki" / "summaries" / "paper.md"
    summary.write_text("# Paper\n", encoding="utf-8")

    result = delete_source.delete_source(str(kb), "paper")

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["document"]["name"] == "paper.pdf"
    assert result["would_remove_pages"] == ["summaries/paper.md"]
    assert summary.exists()
    assert (raw / "paper.pdf").exists()


def test_delete_source_requires_yes_to_mutate(tmp_path):
    delete_source = _load_script("delete_source")
    kb = _make_skill_kb(tmp_path)
    raw = kb / "raw"
    raw.mkdir()
    (kb / ".openkb").mkdir(exist_ok=True)
    (kb / ".openkb" / "hashes.json").write_text(
        json.dumps({"hash-a": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    (raw / "paper.pdf").write_bytes(b"%PDF")
    (kb / "wiki" / "sources").mkdir()
    (kb / "wiki" / "sources" / "paper.md").write_text("# Full", encoding="utf-8")
    summary = kb / "wiki" / "summaries" / "paper.md"
    summary.write_text("# Paper\n", encoding="utf-8")

    result = delete_source.delete_source(str(kb), "paper", yes=True)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["removed_pages"] == ["summaries/paper.md"]
    assert "raw/paper.pdf" in result["removed_files"]
    assert result["commit"]["message"] in {"Delete source paper.pdf", ""}
    assert "delete-source | paper.pdf" in (kb / "wiki" / "log.md").read_text(encoding="utf-8")
    assert not summary.exists()
    assert not (raw / "paper.pdf").exists()
