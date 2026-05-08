"""Tests for bundled openkb-lint-query skill scripts."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "agent-skills"
    / "openkb-lint-query"
    / "scripts"
)


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
