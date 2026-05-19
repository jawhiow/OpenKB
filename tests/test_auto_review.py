"""Tests for openkb/workflows/auto_review.py decision engine and runner."""
from __future__ import annotations

import json
from pathlib import Path

from openkb.document_ledger import get_document_ledger_record, upsert_document_ledger_record
from openkb.workflows.auto_review import (
    AutoReviewConfig,
    Decision,
    auto_review_config,
    decide,
    evaluate_document,
    revert_run,
    run_auto_review,
)
from openkb.workflows.auto_review_overlap import OverlapHit, OverlapResult
from openkb.workflows.auto_review_signals import HardSignals


# -----------------------------------------------------------------------------
# Fixtures / helpers
# -----------------------------------------------------------------------------


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    for sub in ("wiki/sources", "wiki/concepts", "wiki/companies", "wiki/industries", "wiki/explorations",
                "wiki/summaries", ".openkb/review_summaries"):
        (kb_dir / sub).mkdir(parents=True)
    return kb_dir


def _v2_scorecard(**overrides) -> dict:
    base = {
        "version": "v2",
        "method": "llm_summary_value_v2",
        "overall_assessment": "test",
        "total_score": 80,
        "dimensions": {
            "research_depth": {"label": "Research Depth", "score": 12, "max": 15, "reason": ""},
            "durability": {"label": "Durability", "score": 12, "max": 15, "reason": ""},
            "source_coverage": {"label": "Source Coverage", "score": 13, "max": 15, "reason": ""},
            "factual_density": {"label": "Factual Density", "score": 12, "max": 15, "reason": ""},
            "retrieval_value": {"label": "Retrieval Value", "score": 8, "max": 10, "reason": ""},
            "novelty_vs_kb": {"label": "Novelty", "score": 8, "max": 10, "reason": ""},
            "structure_clarity": {"label": "Structure", "score": 4, "max": 5, "reason": ""},
            "actionability": {"label": "Actionability", "score": 4, "max": 5, "reason": ""},
            "cross_linking": {"label": "Cross-linking", "score": 4, "max": 5, "reason": ""},
            "topic_fit": {"label": "Topic Fit", "score": 3, "max": 5, "reason": ""},
        },
    }
    if "dimensions" in overrides:
        for k, v in overrides.pop("dimensions").items():
            base["dimensions"][k]["score"] = v
    base.update(overrides)
    # Recompute total from dims for consistency
    base["total_score"] = sum(d["score"] for d in base["dimensions"].values())
    return base


def _default_cfg() -> AutoReviewConfig:
    return auto_review_config({
        "auto_review": {
            "enabled": True,
            "dry_run": True,
            "approve_threshold": 70,
            "reject_threshold": 50,
            "min_research_depth": 8,
            "min_durability": 6,
            "min_novelty_vs_kb": 5,
            "min_topic_fit": 2,
            "min_source_coverage": 8,
            "daily_approve_budget": 5,
            "overlap": {"reject_threshold": 0.80, "hold_threshold": 0.60},
        }
    })


# -----------------------------------------------------------------------------
# decide() - pure decision logic
# -----------------------------------------------------------------------------


def test_decide_approves_high_score_no_signals():
    doc = {"file_hash": "h1", "name": "深度研究.pdf"}
    sc = _v2_scorecard()
    signals = HardSignals()
    overlap = OverlapResult(max_overlap=0.2)
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "approved"
    assert decision.decision_path == "approve"


def test_decide_holds_when_research_depth_too_low():
    doc = {"file_hash": "h2", "name": "公司点评.pdf"}
    sc = _v2_scorecard(dimensions={"research_depth": 5})  # below min 8
    signals = HardSignals()
    overlap = OverlapResult()
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "held_for_human"
    assert any("low_research_depth" in r for r in decision.reasons)


def test_decide_holds_for_transcript_hard_signal():
    doc = {"file_hash": "h3", "name": "对话纪要.pdf"}
    sc = _v2_scorecard()
    signals = HardSignals(transcript_matches=["对话", "纪要"], research_depth_cap=5)
    overlap = OverlapResult()
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "held_for_human"
    assert any("hard_signal" in r for r in decision.reasons)


def test_decide_rejects_low_total_score():
    doc = {"file_hash": "h4", "name": "thin.pdf"}
    sc = _v2_scorecard(dimensions={"source_coverage": 4, "factual_density": 4, "research_depth": 4})
    sc["total_score"] = 40  # force below reject_threshold=50
    signals = HardSignals()
    overlap = OverlapResult()
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "rejected"
    assert any("low_total_score" in r for r in decision.reasons)


def test_decide_rejects_on_high_overlap():
    doc = {"file_hash": "h5", "name": "重复内容.pdf"}
    sc = _v2_scorecard()
    signals = HardSignals()
    overlap = OverlapResult(
        max_overlap=0.85, top_hits=[OverlapHit(page="concepts/HBM", score=0.85)]
    )
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "rejected"
    assert decision.decision_path == "overlap_reject"
    assert any("duplicate_of" in r for r in decision.reasons)


def test_decide_holds_on_medium_overlap_with_otherwise_passing_score():
    doc = {"file_hash": "h6", "name": "部分重复.pdf"}
    sc = _v2_scorecard()
    signals = HardSignals()
    overlap = OverlapResult(
        max_overlap=0.65, top_hits=[OverlapHit(page="concepts/HBM", score=0.65)]
    )
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "held_for_human"
    assert any("possible_merge_target" in r for r in decision.reasons)


def test_decide_middle_band_holds():
    doc = {"file_hash": "h7", "name": "中等质量.pdf"}
    sc = _v2_scorecard()
    sc["total_score"] = 60  # between reject=50 and approve=70
    signals = HardSignals()
    overlap = OverlapResult()
    cfg = _default_cfg()

    decision = decide(doc, sc, signals, overlap, cfg)
    assert decision.final_decision == "held_for_human"
    assert any("middle_band" in r for r in decision.reasons)


# -----------------------------------------------------------------------------
# run_auto_review() - dry_run vs apply ledger side effects
# -----------------------------------------------------------------------------


def _seed_doc(kb_dir: Path, file_hash: str, name: str, scorecard: dict) -> None:
    stem = Path(name).stem
    # Write a tiny review summary file the runner can read
    summary_dir = kb_dir / ".openkb" / "review_summaries"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / f"{stem}.md").write_text(
        "---\ndoc_type: short\nfull_text: sources/" + stem + ".md\n---\n\n# Summary\n\nContent",
        encoding="utf-8",
    )
    # Also register the source so resolve_source_document works
    (kb_dir / "wiki" / "sources" / f"{stem}.md").write_text(
        "# Source\n\nbody", encoding="utf-8"
    )
    hashes_path = kb_dir / ".openkb" / "hashes.json"
    if hashes_path.exists():
        registry = json.loads(hashes_path.read_text(encoding="utf-8") or "{}")
    else:
        registry = {}
    registry[file_hash] = {"name": name, "type": "md"}
    hashes_path.write_text(json.dumps(registry), encoding="utf-8")
    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "name": name,
            "stem": stem,
            "raw_path": f"raw/{name}",
            "source_path": f"sources/{stem}.md",
            "review_summary_path": f"review_summaries/{stem}.md",
            "source_kind": "markdown",
            "workflow_state": {
                "source_state": "ready",
                "summary_state": "ready",
                "review_state": "unreviewed",
                "promotion_state": "not_selected",
            },
            "review": {
                "summary_score": scorecard.get("total_score"),
                "summary_score_source": "auto",
                "summary_scorecard": scorecard,
            },
        },
    )


def _write_auto_review_config(kb_dir: Path) -> None:
    cfg = {
        "model": "test/model",
        "language": "zh",
        "auto_review": {
            "enabled": True,
            "dry_run": False,
            "approve_threshold": 70,
            "reject_threshold": 50,
            "min_research_depth": 8,
            "min_durability": 6,
            "min_novelty_vs_kb": 5,
            "min_topic_fit": 2,
            "min_source_coverage": 8,
            "daily_approve_budget": 5,
        },
    }
    config_path = kb_dir / ".openkb" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml  # PyYAML is already a project dep
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


def test_run_auto_review_dry_run_does_not_change_ledger(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _write_auto_review_config(kb_dir)
    _seed_doc(kb_dir, "hash-good", "深度研究报告.pdf", _v2_scorecard())

    result = run_auto_review(kb_dir, dry_run=True)
    assert result["dry_run"] is True
    assert result["total"] == 1

    record = get_document_ledger_record(kb_dir, "hash-good")
    assert record["workflow_state"]["review_state"] == "unreviewed"


def test_run_auto_review_apply_sets_review_state(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _write_auto_review_config(kb_dir)
    _seed_doc(kb_dir, "hash-good", "深度研究报告.pdf", _v2_scorecard())

    result = run_auto_review(kb_dir, dry_run=False, operator="test")
    assert result["dry_run"] is False
    assert result["approved"] == 1

    record = get_document_ledger_record(kb_dir, "hash-good")
    assert record["workflow_state"]["review_state"] == "approved"
    assert record["review"]["approved_by"] == "auto_review_v1"


def test_revert_run_resets_review_state(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _write_auto_review_config(kb_dir)
    _seed_doc(kb_dir, "hash-good", "深度研究报告.pdf", _v2_scorecard())

    apply_result = run_auto_review(kb_dir, dry_run=False)
    run_id = apply_result["run_id"]

    revert_result = revert_run(kb_dir, run_id)
    assert revert_result["reverted"] == 1

    record = get_document_ledger_record(kb_dir, "hash-good")
    assert record["workflow_state"]["review_state"] == "unreviewed"
    assert record["review"]["approved_by"] == ""


def test_run_auto_review_emits_progress(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _write_auto_review_config(kb_dir)
    _seed_doc(kb_dir, "hash-good", "深度研究报告.pdf", _v2_scorecard())

    events: list[dict] = []
    run_auto_review(kb_dir, dry_run=True, progress_callback=events.append)
    assert any(e.get("event") == "selected" for e in events)
    assert any(e.get("event") == "decision" for e in events)


def test_run_auto_review_budget_downgrades_extra_approvals(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    _write_auto_review_config(kb_dir)
    # 6 high-quality docs; budget is 5
    for i in range(6):
        _seed_doc(kb_dir, f"hash-{i}", f"深度报告-{i}.pdf", _v2_scorecard())
    # Override budget to 2 via direct config edit
    import yaml
    config_path = kb_dir / ".openkb" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg["auto_review"]["daily_approve_budget"] = 2
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    result = run_auto_review(kb_dir, dry_run=True)
    assert result["approved"] == 2
    assert result["held_for_human"] == 4
