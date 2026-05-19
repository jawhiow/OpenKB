"""Auto-review decision engine and audit log.

Combines three signals:
  1. :mod:`summary_pipeline` LLM scorecard (v2 - 10 dimensions, 100 pts)
  2. :mod:`auto_review_signals` hard signals (filename patterns → dim caps)
  3. :mod:`auto_review_overlap` deterministic jaccard overlap with the wiki

Produces three outcomes: ``approved``, ``rejected``, ``held_for_human``.
Writes an audit-trail entry to ``wiki/explorations/自动审批台账.md`` (matching
the format used by ``openkb.ingest_gate``).
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from openkb.config import DEFAULT_CONFIG, load_config
from openkb.document_ledger import (
    get_document_ledger_record,
    list_effective_document_ledger_records,
    select_document_ledger_records,
    upsert_document_ledger_record,
)
from openkb.workflows.auto_review_overlap import (
    OverlapResult,
    evaluate_overlap,
    overlap_config,
)
from openkb.workflows.auto_review_signals import (
    HardSignals,
    apply_hard_signal_caps,
    detect_hard_signals,
    hard_signals_config,
)
from openkb.workflows.summary_pipeline import read_review_summary


AutoReviewProgressCallback = Callable[[dict[str, Any]], None]
AUTO_REVIEW_HISTORY = "auto_review_history.jsonl"

_AUTO_REVIEW_LOCKS: dict[str, threading.RLock] = {}
_AUTO_REVIEW_LOCKS_GUARD = threading.Lock()


# ---------------------------------------------------------------------------
# Decision configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutoReviewConfig:
    enabled: bool
    dry_run: bool
    approve_threshold: int
    reject_threshold: int
    min_research_depth: int
    min_durability: int
    min_novelty_vs_kb: int
    min_topic_fit: int
    min_source_coverage: int
    daily_approve_budget: int
    overlap_reject: float
    overlap_hold: float

    @property
    def is_active(self) -> bool:
        return self.enabled


def auto_review_config(config: Mapping[str, Any]) -> AutoReviewConfig:
    block: Mapping[str, Any] = {}
    if isinstance(config, Mapping):
        outer = config.get("auto_review")
        if isinstance(outer, Mapping):
            block = outer
    overlap = overlap_config(config)
    return AutoReviewConfig(
        enabled=bool(block.get("enabled", False)),
        dry_run=bool(block.get("dry_run", True)),
        approve_threshold=int(block.get("approve_threshold", 70)),
        reject_threshold=int(block.get("reject_threshold", 50)),
        min_research_depth=int(block.get("min_research_depth", 8)),
        min_durability=int(block.get("min_durability", 6)),
        min_novelty_vs_kb=int(block.get("min_novelty_vs_kb", 5)),
        min_topic_fit=int(block.get("min_topic_fit", 2)),
        min_source_coverage=int(block.get("min_source_coverage", 8)),
        daily_approve_budget=int(block.get("daily_approve_budget", 15)),
        overlap_reject=float(overlap["reject_threshold"]),
        overlap_hold=float(overlap["hold_threshold"]),
    )


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """One auto-review decision for one document."""

    file_hash: str
    name: str
    final_decision: str                 # "approved" | "rejected" | "held_for_human"
    reasons: list[str] = field(default_factory=list)
    total_score: int | None = None
    scorecard_version: str = ""
    hard_signals: dict[str, Any] = field(default_factory=dict)
    overlap: dict[str, Any] = field(default_factory=dict)
    dimension_snapshot: dict[str, int] = field(default_factory=dict)
    decision_path: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_hash": self.file_hash,
            "name": self.name,
            "final_decision": self.final_decision,
            "reasons": list(self.reasons),
            "total_score": self.total_score,
            "scorecard_version": self.scorecard_version,
            "hard_signals": dict(self.hard_signals),
            "overlap": dict(self.overlap),
            "dimension_snapshot": dict(self.dimension_snapshot),
            "decision_path": self.decision_path,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def _dim_score(scorecard: Mapping[str, Any], key: str) -> int:
    dims = scorecard.get("dimensions") if isinstance(scorecard, Mapping) else None
    if not isinstance(dims, Mapping):
        return 0
    raw = dims.get(key)
    if not isinstance(raw, Mapping):
        return 0
    try:
        return int(raw.get("score", 0))
    except (TypeError, ValueError):
        return 0


def _dimension_snapshot(scorecard: Mapping[str, Any]) -> dict[str, int]:
    dims = scorecard.get("dimensions") if isinstance(scorecard, Mapping) else None
    if not isinstance(dims, Mapping):
        return {}
    snapshot: dict[str, int] = {}
    for key, value in dims.items():
        if isinstance(value, Mapping):
            try:
                snapshot[str(key)] = int(value.get("score", 0))
            except (TypeError, ValueError):
                snapshot[str(key)] = 0
    return snapshot


def decide(
    document: Mapping[str, Any],
    scorecard: Mapping[str, Any],
    signals: HardSignals,
    overlap: OverlapResult,
    config: AutoReviewConfig,
) -> Decision:
    """Pure decision function: scorecard + signals + overlap → Decision."""
    file_hash = str(document.get("file_hash") or "").strip()
    name = str(document.get("name") or "").strip()
    total = int(scorecard.get("total_score") or 0)
    version = str(scorecard.get("version") or "").strip()
    reasons: list[str] = []
    path_parts: list[str] = []

    # 0. Hard overlap → reject (deterministic duplicate)
    if overlap.max_overlap >= config.overlap_reject:
        path_parts.append("overlap_reject")
        target = overlap.top_hits[0].page if overlap.top_hits else "unknown"
        reasons.append(f"duplicate_of:{target} (overlap={overlap.max_overlap:.2f})")
        return _build(
            file_hash, name, "rejected", reasons, total, version, signals,
            overlap, scorecard, "/".join(path_parts)
        )

    # 1. Hard dimension floors → HOLD
    research_depth = _dim_score(scorecard, "research_depth")
    durability = _dim_score(scorecard, "durability")
    novelty = _dim_score(scorecard, "novelty_vs_kb")
    topic_fit = _dim_score(scorecard, "topic_fit")
    source_cov = _dim_score(scorecard, "source_coverage")

    if version == "v2":
        if research_depth < config.min_research_depth:
            reasons.append(f"low_research_depth:{research_depth}/<{config.min_research_depth}")
        if durability < config.min_durability:
            reasons.append(f"low_durability:{durability}/<{config.min_durability}")
        if novelty < config.min_novelty_vs_kb:
            reasons.append(f"low_novelty_vs_kb:{novelty}/<{config.min_novelty_vs_kb}")
        if topic_fit < config.min_topic_fit:
            reasons.append(f"low_topic_fit:{topic_fit}/<{config.min_topic_fit}")
    if source_cov < config.min_source_coverage:
        reasons.append(f"low_source_coverage:{source_cov}/<{config.min_source_coverage}")

    if config.overlap_hold > 0 and overlap.max_overlap >= config.overlap_hold:
        # Overlap-hold band: possible merge target rather than full duplicate.
        target = overlap.top_hits[0].page if overlap.top_hits else "unknown"
        reasons.append(f"possible_merge_target:{target} (overlap={overlap.max_overlap:.2f})")
        path_parts.append("overlap_hold")

    # 2. Total-score floor → reject
    if total < config.reject_threshold:
        path_parts.append("low_total_reject")
        reasons.insert(0, f"low_total_score:{total}/<{config.reject_threshold}")
        return _build(
            file_hash, name, "rejected", reasons, total, version, signals,
            overlap, scorecard, "/".join(path_parts)
        )

    # 3. Hard signals (transcript / ephemeral / news) → HOLD
    if signals.any_match:
        markers = []
        if signals.ephemeral_matches:
            markers.append("ephemeral:" + "|".join(signals.ephemeral_matches))
        if signals.transcript_matches:
            markers.append("transcript:" + "|".join(signals.transcript_matches))
        if signals.news_matches:
            markers.append("news:" + "|".join(signals.news_matches))
        reasons.append("hard_signal:" + ";".join(markers))
        path_parts.append("hard_signal_hold")
        return _build(
            file_hash, name, "held_for_human", reasons, total, version, signals,
            overlap, scorecard, "/".join(path_parts)
        )

    # 4. If any HOLD-class reason accumulated → HOLD
    if reasons:
        path_parts.append("dim_floor_hold")
        return _build(
            file_hash, name, "held_for_human", reasons, total, version, signals,
            overlap, scorecard, "/".join(path_parts)
        )

    # 5. Total-score ceiling → APPROVE
    if total >= config.approve_threshold:
        path_parts.append("approve")
        reasons.append(f"passed_all_gates:total={total}≥{config.approve_threshold}")
        return _build(
            file_hash, name, "approved", reasons, total, version, signals,
            overlap, scorecard, "/".join(path_parts)
        )

    # 6. Middle band → HOLD
    path_parts.append("middle_band_hold")
    reasons.append(f"middle_band:{config.reject_threshold}≤{total}<{config.approve_threshold}")
    return _build(
        file_hash, name, "held_for_human", reasons, total, version, signals,
        overlap, scorecard, "/".join(path_parts)
    )


def _build(
    file_hash: str,
    name: str,
    final: str,
    reasons: list[str],
    total: int,
    version: str,
    signals: HardSignals,
    overlap: OverlapResult,
    scorecard: Mapping[str, Any],
    decision_path: str,
) -> Decision:
    return Decision(
        file_hash=file_hash,
        name=name,
        final_decision=final,
        reasons=reasons,
        total_score=total,
        scorecard_version=version,
        hard_signals=signals.to_dict(),
        overlap=overlap.to_dict() if hasattr(overlap, "to_dict") else dict(overlap),  # type: ignore[arg-type]
        dimension_snapshot=_dimension_snapshot(scorecard),
        decision_path=decision_path,
        timestamp=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Driver: evaluate one document end-to-end
# ---------------------------------------------------------------------------


def evaluate_document(
    kb_dir: Path,
    file_hash: str,
    *,
    config: AutoReviewConfig | None = None,
    raw_config: Mapping[str, Any] | None = None,
) -> Decision:
    """Resolve a ledger record and produce one Decision (no side effects)."""
    record = get_document_ledger_record(kb_dir, file_hash)
    if record is None:
        effective = list_effective_document_ledger_records(kb_dir)
        record = effective.get(file_hash)
    if record is None:
        raise RuntimeError(f"Document not found in ledger: {file_hash}")
    name = str(record.get("name") or "")
    stem = str(record.get("stem") or "")

    raw_cfg = raw_config or load_config(kb_dir / ".openkb" / "config.yaml")
    cfg = config or auto_review_config(raw_cfg)

    scorecard = record.get("review", {}).get("summary_scorecard") or {}
    if not isinstance(scorecard, Mapping) or not scorecard:
        return Decision(
            file_hash=file_hash,
            name=name,
            final_decision="held_for_human",
            reasons=["missing_scorecard"],
            scorecard_version="",
            timestamp=_now_iso(),
        )

    # Apply filename-based hard caps before deciding.
    signals = detect_hard_signals(name, config=raw_cfg)
    capped = apply_hard_signal_caps(scorecard, signals)
    if isinstance(capped, Mapping):
        scorecard = capped

    # Compute deterministic overlap when summary text is available.
    summary_text = ""
    try:
        summary_text, _rel = read_review_summary(
            kb_dir,
            stem=stem,
            ingested_at=record.get("ingested_at"),
            review_summary_path=str(record.get("review_summary_path") or ""),
        )
    except Exception:
        summary_text = ""
    overlap_result = evaluate_overlap(summary_text, kb_dir) if summary_text else OverlapResult()

    decision = decide(record, scorecard, signals, overlap_result, cfg)
    # Stash hold_threshold so we can show it in audit later if needed.
    decision.overlap["hold_threshold"] = cfg.overlap_hold
    decision.overlap["reject_threshold"] = cfg.overlap_reject
    return decision


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------


def run_auto_review(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None = None,
    dry_run: bool | None = None,
    progress_callback: AutoReviewProgressCallback | None = None,
    operator: str = "",
) -> dict[str, Any]:
    """Run auto-review across selected documents.

    Selection rule when ``file_hashes`` is None: review_state=unreviewed AND
    summary_state=ready. ``dry_run`` defaults to the configured value.
    """
    raw_cfg = load_config(kb_dir / ".openkb" / "config.yaml")
    cfg = auto_review_config(raw_cfg)
    is_dry = cfg.dry_run if dry_run is None else bool(dry_run)
    run_id = "auto_run_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]

    selected = _selected_hashes(kb_dir, file_hashes)
    decisions: list[Decision] = []
    counters = {"approved": 0, "rejected": 0, "held_for_human": 0, "skipped": 0, "errors": 0}
    daily_budget_left = cfg.daily_approve_budget

    def emit(event: str, **payload: Any) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "run_id": run_id, **payload})

    emit("selected", total=len(selected), dry_run=is_dry)

    for index, file_hash in enumerate(selected, 1):
        try:
            decision = evaluate_document(kb_dir, file_hash, config=cfg, raw_config=raw_cfg)
        except Exception as exc:
            counters["errors"] += 1
            emit("error", index=index, file_hash=file_hash, error=str(exc))
            continue

        # Daily budget guard: downgrade extra approvals to HOLD.
        if decision.final_decision == "approved" and daily_budget_left <= 0:
            decision.final_decision = "held_for_human"
            decision.reasons.append("daily_budget_exhausted")
            decision.decision_path = (decision.decision_path or "") + "/budget_hold"
        elif decision.final_decision == "approved":
            daily_budget_left -= 1

        decisions.append(decision)
        counters[decision.final_decision] += 1

        if not is_dry:
            _apply_decision(kb_dir, decision, operator=operator, run_id=run_id)

        emit(
            "decision",
            index=index,
            file_hash=file_hash,
            final=decision.final_decision,
            total=decision.total_score,
            reasons=decision.reasons,
        )

    lock = _auto_review_lock(kb_dir)
    with lock:
        _append_history(kb_dir, run_id, decisions, dry_run=is_dry, operator=operator)
        _append_audit_page(kb_dir, run_id, decisions, dry_run=is_dry, language=str(raw_cfg.get("language") or DEFAULT_CONFIG["language"]))

    return {
        "run_id": run_id,
        "dry_run": is_dry,
        "total": len(selected),
        "approved": counters["approved"],
        "rejected": counters["rejected"],
        "held_for_human": counters["held_for_human"],
        "errors": counters["errors"],
        "decisions": [d.to_dict() for d in decisions],
    }


def _apply_decision(kb_dir: Path, decision: Decision, *, operator: str, run_id: str) -> None:
    """Persist a single decision into the document ledger."""
    if decision.final_decision == "approved":
        new_state = "approved"
    elif decision.final_decision == "rejected":
        new_state = "rejected"
    else:
        new_state = "unreviewed"  # held_for_human preserves the unreviewed state

    review_block: dict[str, Any] = {
        "review_notes": _short_reason(decision),
        "approved_by": "auto_review_v1" if new_state == "approved" else "",
    }
    if new_state == "approved":
        review_block["approved_at"] = decision.timestamp

    upsert_document_ledger_record(
        kb_dir,
        decision.file_hash,
        {
            "workflow_state": {"review_state": new_state},
            "review": review_block,
            "execution": {"updated_at": _now_iso()},
        },
    )


def _selected_hashes(kb_dir: Path, file_hashes: list[str] | None) -> list[str]:
    if file_hashes:
        return [str(h).strip() for h in file_hashes if str(h).strip()]
    return [
        record["file_hash"]
        for record in select_document_ledger_records(
            kb_dir,
            review_state="unreviewed",
            summary_state="ready",
        )
    ]


def _short_reason(decision: Decision) -> str:
    reasons = ", ".join(decision.reasons[:3])
    suffix = ""
    if len(decision.reasons) > 3:
        suffix = f" (+{len(decision.reasons) - 3} more)"
    return f"[{decision.final_decision}] {reasons}{suffix}"


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def _append_history(
    kb_dir: Path,
    run_id: str,
    decisions: Iterable[Decision],
    *,
    dry_run: bool,
    operator: str,
) -> None:
    path = kb_dir / ".openkb" / AUTO_REVIEW_HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for d in decisions:
            payload = d.to_dict()
            payload["run_id"] = run_id
            payload["dry_run"] = dry_run
            payload["operator"] = operator
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _audit_page_path(kb_dir: Path, language: str) -> tuple[Path, str]:
    if str(language).lower().startswith("zh"):
        rel = "explorations/自动审批台账.md"
    else:
        rel = "explorations/auto_review_log.md"
    return kb_dir / "wiki" / rel, rel[: -len(".md")]


def _ensure_audit_page(path: Path, language: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(language).lower().startswith("zh"):
        title = "# 自动审批台账\n\n记录 `openkb auto-review` 的所有决策与原因。\n\n"
    else:
        title = "# Auto-review Audit Log\n\nRecords decisions made by `openkb auto-review`.\n\n"
    path.write_text(title, encoding="utf-8")


def _append_audit_page(
    kb_dir: Path,
    run_id: str,
    decisions: Iterable[Decision],
    *,
    dry_run: bool,
    language: str,
) -> None:
    page_path, _page_id = _audit_page_path(kb_dir, language)
    _ensure_audit_page(page_path, language)
    decisions = list(decisions)
    if not decisions:
        return

    lines: list[str] = []
    header_mode = "DRY-RUN" if dry_run else "APPLY"
    counts = {"approved": 0, "rejected": 0, "held_for_human": 0}
    for d in decisions:
        if d.final_decision in counts:
            counts[d.final_decision] += 1
    lines.append(
        f"## [{_now_iso()}] {run_id} | {header_mode} | "
        f"approved={counts['approved']} rejected={counts['rejected']} "
        f"held={counts['held_for_human']} (n={len(decisions)})"
    )
    for d in decisions:
        lines.append(
            f"- **{d.final_decision.upper()}** [{d.total_score or 'n/a'}] "
            f"`{d.file_hash[:10]}` {d.name}"
        )
        if d.decision_path:
            lines.append(f"  - path: `{d.decision_path}`")
        if d.reasons:
            lines.append("  - reasons:")
            lines.extend(f"    - {r}" for r in d.reasons)
        if d.dimension_snapshot:
            keys = ("research_depth", "durability", "novelty_vs_kb", "topic_fit", "source_coverage")
            ordered = [f"{k}={d.dimension_snapshot.get(k, '-')}" for k in keys if k in d.dimension_snapshot]
            if ordered:
                lines.append("  - dims: " + " | ".join(ordered))
        if d.hard_signals.get("any_match"):
            lines.append(f"  - hard_signals: {d.hard_signals}")
        if d.overlap.get("max_overlap"):
            lines.append(f"  - overlap: {d.overlap.get('max_overlap')} top={d.overlap.get('top_hits')}")
    lines.append("")
    with page_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n\n")


def revert_run(kb_dir: Path, run_id: str) -> dict[str, Any]:
    """Revert all decisions made by a given run_id.

    Sets review_state back to ``unreviewed`` for documents that were
    approved/rejected by the named run. The audit log is not rewritten — a
    revert note is appended instead.
    """
    path = kb_dir / ".openkb" / AUTO_REVIEW_HISTORY
    if not path.exists():
        return {"run_id": run_id, "reverted": 0, "errors": 0}

    reverted = 0
    errors = 0
    matching: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("run_id") != run_id or entry.get("dry_run"):
            continue
        matching.append(entry)

    for entry in matching:
        file_hash = str(entry.get("file_hash") or "").strip()
        if not file_hash:
            continue
        try:
            upsert_document_ledger_record(
                kb_dir,
                file_hash,
                {
                    "workflow_state": {"review_state": "unreviewed"},
                    "review": {
                        "review_notes": f"reverted by run_id={run_id}",
                        "approved_by": "",
                        "approved_at": None,
                    },
                    "execution": {"updated_at": _now_iso()},
                },
            )
            reverted += 1
        except Exception:
            errors += 1

    return {"run_id": run_id, "reverted": reverted, "errors": errors}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_review_lock(kb_dir: Path) -> threading.RLock:
    key = str(Path(kb_dir).resolve())
    with _AUTO_REVIEW_LOCKS_GUARD:
        lock = _AUTO_REVIEW_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _AUTO_REVIEW_LOCKS[key] = lock
        return lock


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
