"""Summary-review workflow for staged OpenKB documents."""
from __future__ import annotations

import concurrent.futures
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from openkb.agent.compiler import (
    _SYSTEM_TEMPLATE,
    _build_local_long_doc_context,
    _llm_call,
    _parse_json,
)
from openkb.config import DEFAULT_CONFIG, load_config
from openkb.document_ledger import (
    build_document_ledger_record,
    get_document_ledger_record,
    infer_document_ledger_defaults,
    select_document_ledger_records,
    update_document_workflow_state,
    upsert_document_ledger_record,
)
from openkb.schema import get_agents_md
from openkb.source_relations import (
    normalize_kb_relative_path,
    resolve_source_artifact_path,
    resolve_source_document,
    review_summary_full_path,
    review_summary_relative_path,
)


SummaryProgressCallback = Callable[[dict[str, Any]], None]
SummaryWorker = Callable[[str], dict[str, Any]]
_SUMMARY_LEDGER_LOCKS: dict[Path, threading.RLock] = {}
_SUMMARY_LEDGER_LOCKS_LOCK = threading.Lock()


_SUMMARY_SCORE_DIMENSIONS: tuple[tuple[str, str, int], ...] = (
    ("source_coverage", "Source Coverage", 25),
    ("factual_density", "Factual Density", 20),
    ("structure_clarity", "Structure & Clarity", 15),
    ("retrieval_value", "Retrieval Value", 20),
    ("actionability", "Actionability", 10),
    ("cross_linking", "Cross-linking", 10),
)

_SUMMARY_SCORE_DIMENSION_MAX = {name: max_score for name, _label, max_score in _SUMMARY_SCORE_DIMENSIONS}

_SUMMARY_WITH_SCORE_USER = """\
New document: {doc_name}

Full text:
{content}

Write a review-stage summary page for this document in Markdown, and score the
document's summary value for the knowledge base.

Scoring intent:
- This is NOT an ingest gate score.
- Judge the document on how valuable, information-rich, well-structured, and
  reusable it is after summarization for a long-lived KB.
- Reward concrete facts, durable insights, monitoring indicators, and strong
  traceability.
- Penalize vague, repetitive, low-signal, poorly structured, or weakly
  evidenced content.

For investment research reports, use an investment-research structure when the
source supports it:
- Core thesis and conclusion
- Ratings / top ideas / company table when available
- Key numbers, assumptions, forecasts, and valuation context
- Industry chain map and bottlenecks
- Catalysts and monitoring indicators
- Risks, bear-case evidence, and disconfirming signals
- Source evidence with page references when page markers are present

Keep all material claims traceable to the source text. Preserve important
numbers, dates, companies, and units. Use [[concepts/...]] only for concepts
that deserve durable cross-document pages.

Return a JSON object with these keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown
- "scorecard": an object with:
  - "method": short label for the scoring method
  - "overall_assessment": one short paragraph explaining the score
  - "total_score": integer 0-100
  - "dimensions": object with exactly these keys:
    - "source_coverage": {{"score": 0-25, "reason": ""}}
    - "factual_density": {{"score": 0-20, "reason": ""}}
    - "structure_clarity": {{"score": 0-15, "reason": ""}}
    - "retrieval_value": {{"score": 0-20, "reason": ""}}
    - "actionability": {{"score": 0-10, "reason": ""}}
    - "cross_linking": {{"score": 0-10, "reason": ""}}

Scoring rubric:
- source_coverage: how completely the summary captures the source's major sections and claims
- factual_density: how many concrete numbers, entities, dates, assumptions, and source-backed facts it preserves
- structure_clarity: whether the summary is easy to scan and logically organized
- retrieval_value: whether future querying and recall will benefit from the summary
- actionability: whether it preserves decisions, indicators, risks, catalysts, or next-step value
- cross_linking: whether it identifies durable concepts suitable for KB reuse

Return ONLY valid JSON, no fences.
"""

_LOCAL_LONG_DOC_SUMMARY_WITH_SCORE_USER = """\
This is a page-indexed local extraction for long document "{doc_name}".

{content}

Based on this page-indexed extraction, write a high-signal review-stage summary
page and score the document's summary value for the knowledge base.

Scoring intent:
- This is NOT an ingest gate score.
- Focus on how useful the resulting summary will be for long-term knowledge
  retrieval, synthesis, and downstream wiki generation.
- Reward coverage, factual precision, durable insights, and good structure.
- Penalize thin, repetitive, generic, or poorly evidenced summaries.

For investment research reports, preserve ratings, company names, forecasts,
valuation context, key numbers, catalysts, risks, and monitoring indicators.
Use page references like "p.12" where evidence is available.

Return a JSON object with these keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown with durable [[concepts/...]] links
- "scorecard": an object with:
  - "method": short label for the scoring method
  - "overall_assessment": one short paragraph explaining the score
  - "total_score": integer 0-100
  - "dimensions": object with exactly these keys:
    - "source_coverage": {{"score": 0-25, "reason": ""}}
    - "factual_density": {{"score": 0-20, "reason": ""}}
    - "structure_clarity": {{"score": 0-15, "reason": ""}}
    - "retrieval_value": {{"score": 0-20, "reason": ""}}
    - "actionability": {{"score": 0-10, "reason": ""}}
    - "cross_linking": {{"score": 0-10, "reason": ""}}

Return ONLY valid JSON, no fences.
"""


def _strip_summary_frontmatter(summary: str) -> str:
    if summary.startswith("---"):
        end = summary.find("---", 3)
        if end != -1:
            return summary[end + 3:].lstrip("\n")
    return summary


def _review_summary_doc_type(source_path: Path) -> str:
    return "pageindex" if source_path.suffix.lower() == ".json" else "short"


def _review_summary_text(summary: str, *, doc_type: str, full_text_path: str) -> str:
    frontmatter = "---\n" + "\n".join(
        [
            f"doc_type: {doc_type}",
            f"full_text: {normalize_kb_relative_path(full_text_path)}",
        ]
    ) + "\n---\n\n"
    return frontmatter + _strip_summary_frontmatter(summary)


def write_review_summary(
    kb_dir: Path,
    stem: str,
    summary: str,
    *,
    doc_type: str,
    full_text_path: str,
    ingested_at: object = None,
) -> str:
    path = review_summary_full_path(kb_dir, stem, ingested_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _review_summary_text(summary, doc_type=doc_type, full_text_path=full_text_path),
        encoding="utf-8",
    )
    return review_summary_relative_path(stem, ingested_at)


def read_review_summary(
    kb_dir: Path,
    *,
    stem: str,
    ingested_at: object = None,
    review_summary_path: str = "",
) -> tuple[str, str]:
    candidates: list[tuple[Path, str]] = []
    stored_rel = str(review_summary_path or "").strip().replace("\\", "/").lstrip("/")
    if stored_rel:
        candidates.append((Path(kb_dir) / ".openkb" / stored_rel, stored_rel))
    generated_rel = review_summary_relative_path(stem, ingested_at)
    candidates.append((review_summary_full_path(kb_dir, stem, ingested_at), generated_rel))
    legacy_rel = f"summaries/{stem}.md"
    candidates.append((Path(kb_dir) / "wiki" / legacy_rel, legacy_rel))
    for path, relative in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8"), relative
    raise RuntimeError(f"Review summary is missing for {stem}")


def summarize_document_source(
    kb_dir: Path,
    selector: str,
    *,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate a single-document summary without promoting wiki concepts."""
    kb_dir = Path(kb_dir)
    document = resolve_source_document(kb_dir, selector)
    file_hash = str(document["hash"])
    stem = str(document["stem"])

    with _summary_ledger_lock(kb_dir):
        ledger_record = get_document_ledger_record(kb_dir, file_hash)
        if ledger_record is None:
            ledger_record = build_document_ledger_record(
                file_hash,
                defaults=infer_document_ledger_defaults(document),
            )
        workflow_state = ledger_record.get("workflow_state", {})
        if workflow_state.get("summary_state") == "ready" and not force:
            return {
                "file_hash": file_hash,
                "name": document["name"],
                "skipped": True,
                "summary_path": str(ledger_record.get("review_summary_path") or review_summary_relative_path(stem, document.get("ingested_at"))),
            }
        if workflow_state.get("source_state") not in {"ready", None, ""} and not force:
            raise RuntimeError(f"Source is not ready for summarization: {document['name']}")

        update_document_workflow_state(kb_dir, file_hash, {"summary_state": "running"})

    try:
        source_rel = normalize_kb_relative_path(document.get("source_path"))
        if not source_rel:
            raise RuntimeError(f"No source artifact found for {document['name']}")
        source_path = resolve_source_artifact_path(kb_dir, source_rel)
        if not source_path.exists():
            raise RuntimeError(f"Source artifact is missing: {source_rel}")
        selected_model = model or _default_model(kb_dir)
        generated = _generate_summary_only(kb_dir, stem, source_path, selected_model)
        summary = generated["content"]
        doc_type = _review_summary_doc_type(source_path)
        summary_rel = write_review_summary(
            kb_dir,
            stem,
            summary,
            doc_type=doc_type,
            full_text_path=source_rel,
            ingested_at=document.get("ingested_at"),
        )
    except Exception as exc:
        with _summary_ledger_lock(kb_dir):
            _mark_summary_failed(kb_dir, file_hash, exc)
        raise

    with _summary_ledger_lock(kb_dir):
        upsert_document_ledger_record(
            kb_dir,
            file_hash,
            {
                "review_summary_path": summary_rel,
                "workflow_state": {
                    "summary_state": "ready",
                    "review_state": "unreviewed",
                    "promotion_state": "not_selected",
                },
                "review": {
                    "summary_score": generated["scorecard"].get("total_score"),
                    "summary_score_source": "auto",
                    "summary_scorecard": generated["scorecard"],
                },
                "execution": {
                    "last_error": "",
                    "updated_at": _now_iso(),
                },
            },
        )
    return {
        "file_hash": file_hash,
        "name": document["name"],
        "skipped": False,
        "summary_path": summary_rel,
    }


def summarize_documents(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None = None,
    model: str | None = None,
    force: bool = False,
    max_workers: int = 1,
    progress_callback: SummaryProgressCallback | None = None,
    worker: SummaryWorker | None = None,
) -> dict[str, Any]:
    """Batch-generate summaries for selected or source-ready documents."""
    selected_hashes = _selected_summary_hashes(kb_dir, file_hashes=file_hashes)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    generated = 0
    skipped = 0
    total = len(selected_hashes)
    completed = 0
    state_lock = threading.Lock()

    def emit(event: str, **payload: Any) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "completed": completed, "total": total, **payload})

    def run_one(index: int, file_hash: str) -> tuple[int, str, dict[str, Any] | None, Exception | None]:
        emit("start", index=index, file_hash=file_hash)
        try:
            if worker is not None:
                result = worker(file_hash)
            else:
                result = summarize_document_source(kb_dir, file_hash, model=model, force=force)
        except Exception as exc:
            return index, file_hash, None, exc
        return index, file_hash, result, None

    def record(index: int, file_hash: str, result: dict[str, Any] | None, exc: Exception | None) -> None:
        nonlocal completed, generated, skipped
        with state_lock:
            completed += 1
            if exc is not None:
                failures.append({"file_hash": file_hash, "error": str(exc)})
                emit("failure", index=index, file_hash=file_hash, error=str(exc))
                return
            assert result is not None
            results.append(result)
            if result.get("skipped"):
                skipped += 1
                emit("skipped", index=index, file_hash=file_hash, name=str(result.get("name") or ""))
            else:
                generated += 1
                emit(
                    "generated",
                    index=index,
                    file_hash=file_hash,
                    name=str(result.get("name") or ""),
                    summary_path=str(result.get("summary_path") or ""),
                )

    emit("selected")
    worker_count = min(max(int(max_workers or 1), 1), max(total, 1))
    if worker_count == 1:
        for index, file_hash in enumerate(selected_hashes, 1):
            record(*run_one(index, file_hash))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(run_one, index, file_hash)
                for index, file_hash in enumerate(selected_hashes, 1)
            ]
            for future in concurrent.futures.as_completed(futures):
                record(*future.result())
    return {
        "generated": generated,
        "skipped": skipped,
        "failed": len(failures),
        "total": total,
        "failures": failures,
        "documents": results,
    }


def update_summary_review(
    kb_dir: Path,
    file_hash: str,
    *,
    review_state: str,
    summary_score: int | None = None,
    review_notes: str = "",
    approved_by: str = "",
) -> dict[str, Any]:
    """Update review metadata for one generated summary."""
    updates: dict[str, Any] = {
        "workflow_state": {"review_state": review_state},
        "review": {"review_notes": review_notes},
        "execution": {"updated_at": _now_iso()},
    }
    if summary_score is not None:
        updates["review"]["summary_score"] = summary_score
        updates["review"]["summary_score_source"] = "manual"
    if approved_by:
        updates["review"]["approved_by"] = approved_by
    if review_state == "approved":
        updates["review"]["approved_at"] = _now_iso()
    return upsert_document_ledger_record(kb_dir, file_hash, updates)


def update_summary_reviews(
    kb_dir: Path,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Batch-update summary review metadata."""
    updated: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for review in reviews:
        file_hash = str(review.get("file_hash") or "").strip()
        if not file_hash:
            failures.append({"file_hash": "", "error": "file_hash is required"})
            continue
        try:
            record = update_summary_review(
                kb_dir,
                file_hash,
                review_state=str(review.get("review_state") or "scored").strip(),
                summary_score=_optional_int(review.get("summary_score")),
                review_notes=str(review.get("review_notes") or "").strip(),
                approved_by=str(review.get("approved_by") or "").strip(),
            )
        except Exception as exc:
            failures.append({"file_hash": file_hash, "error": str(exc)})
            continue
        updated.append(record)
    return {
        "updated": len(updated),
        "failed": len(failures),
        "total": len(reviews),
        "failures": failures,
        "documents": updated,
    }


def _generate_summary_only(kb_dir: Path, doc_name: str, source_path: Path, model: str) -> dict[str, Any]:
    wiki_dir = kb_dir / "wiki"
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language = str(config.get("language") or DEFAULT_CONFIG["language"])
    system_msg = {
        "role": "system",
        "content": _SYSTEM_TEMPLATE.format(schema_md=get_agents_md(wiki_dir), language=language),
    }
    if source_path.suffix.lower() == ".json":
        content = _build_local_long_doc_context(source_path)
        doc_msg = {
            "role": "user",
            "content": _LOCAL_LONG_DOC_SUMMARY_WITH_SCORE_USER.format(doc_name=doc_name, content=content),
        }
        step_name = "local-long-summary"
    else:
        content = source_path.read_text(encoding="utf-8")
        doc_msg = {
            "role": "user",
            "content": _SUMMARY_WITH_SCORE_USER.format(doc_name=doc_name, content=content),
        }
        step_name = "summary-only"

    raw = _llm_call(model, [system_msg, doc_msg], step_name)
    try:
        parsed = _parse_json(raw)
        if isinstance(parsed, dict):
            summary = str(parsed.get("content") or raw)
            scorecard = _normalize_summary_scorecard(parsed.get("scorecard"), summary)
            return {
                "brief": str(parsed.get("brief") or "").strip(),
                "content": summary,
                "scorecard": scorecard,
            }
    except (json.JSONDecodeError, ValueError):
        pass
    summary = raw
    return {
        "brief": "",
        "content": summary,
        "scorecard": _fallback_summary_scorecard(summary),
    }


def _selected_summary_hashes(kb_dir: Path, *, file_hashes: list[str] | None) -> list[str]:
    if file_hashes is not None:
        return [str(file_hash).strip() for file_hash in file_hashes if str(file_hash).strip()]
    return [
        record["file_hash"]
        for record in select_document_ledger_records(
            kb_dir,
            source_state="ready",
            summary_state=["not_started", "failed"],
        )
    ]


def _summary_ledger_lock(kb_dir: Path) -> threading.RLock:
    key = Path(kb_dir).resolve()
    with _SUMMARY_LEDGER_LOCKS_LOCK:
        lock = _SUMMARY_LEDGER_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SUMMARY_LEDGER_LOCKS[key] = lock
        return lock


def _mark_summary_failed(kb_dir: Path, file_hash: str, error: Exception) -> None:
    existing = get_document_ledger_record(kb_dir, file_hash)
    retry_count = int((existing or {}).get("execution", {}).get("retry_count") or 0) + 1
    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "workflow_state": {"summary_state": "failed"},
            "execution": {
                "last_error": str(error),
                "retry_count": retry_count,
                "updated_at": _now_iso(),
            },
        },
    )


def _normalize_summary_scorecard(raw: Any, summary: str) -> dict[str, Any]:
    fallback = _fallback_summary_scorecard(summary)
    if not isinstance(raw, dict):
        return fallback
    dimensions_raw = raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else {}
    dimensions: dict[str, dict[str, Any]] = {}
    total = 0
    for name, label, max_score in _SUMMARY_SCORE_DIMENSIONS:
        dimension_raw = dimensions_raw.get(name) if isinstance(dimensions_raw.get(name), dict) else {}
        score = _bounded_int(dimension_raw.get("score"), minimum=0, maximum=max_score)
        reason = str(dimension_raw.get("reason") or "").strip()
        dimensions[name] = {
            "label": label,
            "score": score,
            "max": max_score,
            "reason": reason,
        }
        total += score

    stated_total = _bounded_int(raw.get("total_score"), minimum=0, maximum=100)
    overall = str(raw.get("overall_assessment") or "").strip()
    method = str(raw.get("method") or "").strip() or "llm_summary_value_v1"
    if not overall:
        overall = fallback["overall_assessment"]
    return {
        "method": method,
        "overall_assessment": overall,
        "total_score": total if stated_total != total else stated_total,
        "dimensions": dimensions,
    }


def _fallback_summary_scorecard(summary: str) -> dict[str, Any]:
    text = str(summary or "")
    line_count = len([line for line in text.splitlines() if line.strip()])
    bullet_count = text.count("\n- ") + text.count("\n* ")
    wiki_link_count = text.count("[[")
    digit_count = sum(char.isdigit() for char in text)
    heading_count = sum(1 for line in text.splitlines() if line.strip().startswith("#"))
    sentences = [part.strip() for part in text.replace("\n", " ").split("。")]
    sentences = [part for part in sentences if part]

    dimensions = {
        "source_coverage": {
            "label": "Source Coverage",
            "score": min(25, 8 + min(line_count, 24) // 2 + min(heading_count, 3) * 2),
            "max": 25,
            "reason": "Heuristic estimate based on summary breadth, sectioning, and coverage signals.",
        },
        "factual_density": {
            "label": "Factual Density",
            "score": min(20, 4 + min(digit_count, 32) // 3),
            "max": 20,
            "reason": "Heuristic estimate based on preserved numeric and concrete source-backed detail.",
        },
        "structure_clarity": {
            "label": "Structure & Clarity",
            "score": min(15, 5 + min(heading_count, 4) * 2 + min(bullet_count, 8) // 2),
            "max": 15,
            "reason": "Heuristic estimate based on headings, list structure, and scanability.",
        },
        "retrieval_value": {
            "label": "Retrieval Value",
            "score": min(20, 6 + min(line_count, 18) // 2 + min(len(sentences), 10) // 2),
            "max": 20,
            "reason": "Heuristic estimate of how useful the summary will be for later recall and querying.",
        },
        "actionability": {
            "label": "Actionability",
            "score": min(10, 2 + min(bullet_count, 10) // 2 + (2 if digit_count >= 6 else 0)),
            "max": 10,
            "reason": "Heuristic estimate based on whether the summary likely preserves indicators, risks, or decisions.",
        },
        "cross_linking": {
            "label": "Cross-linking",
            "score": min(10, 1 + min(wiki_link_count, 9)),
            "max": 10,
            "reason": "Heuristic estimate based on reusable concept linking signals in the summary.",
        },
    }
    total = sum(int(item["score"]) for item in dimensions.values())
    return {
        "method": "heuristic_summary_value_v1",
        "overall_assessment": (
            "Fallback score derived from summary structure, factual detail density, "
            "and reuse signals because a structured LLM scorecard was unavailable."
        ),
        "total_score": total,
        "dimensions": dimensions,
    }


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, numeric))
def _default_model(kb_dir: Path) -> str:
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    return str(config.get("model") or DEFAULT_CONFIG["model"])


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
