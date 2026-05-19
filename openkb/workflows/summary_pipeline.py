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
    ("research_depth", "Research Depth", 15),
    ("durability", "Durability", 15),
    ("source_coverage", "Source Coverage", 15),
    ("factual_density", "Factual Density", 15),
    ("retrieval_value", "Retrieval Value", 10),
    ("novelty_vs_kb", "Novelty vs KB", 10),
    ("structure_clarity", "Structure & Clarity", 5),
    ("actionability", "Actionability", 5),
    ("cross_linking", "Cross-linking", 5),
    ("topic_fit", "Topic Fit", 5),
)

_SUMMARY_SCORE_DIMENSION_MAX = {name: max_score for name, _label, max_score in _SUMMARY_SCORE_DIMENSIONS}
_SUMMARY_SCORECARD_VERSION = "v2"

_LEGACY_SUMMARY_SCORE_DIMENSIONS_V1: tuple[tuple[str, str, int], ...] = (
    ("source_coverage", "Source Coverage", 25),
    ("factual_density", "Factual Density", 20),
    ("structure_clarity", "Structure & Clarity", 15),
    ("retrieval_value", "Retrieval Value", 20),
    ("actionability", "Actionability", 10),
    ("cross_linking", "Cross-linking", 10),
)
_LEGACY_SUMMARY_SCORE_DIMENSION_MAX_V1 = {
    name: max_score for name, _label, max_score in _LEGACY_SUMMARY_SCORE_DIMENSIONS_V1
}

_SUMMARY_SCORE_RUBRIC = """\
Scoring rubric (v2). Each dimension has explicit anchors — DO NOT default to high scores.
Read the anchors and pick the band that best matches the document. Be honest about weaknesses.

- research_depth (0-15): Is this a research artifact or a news/transcript snippet?
    13-15 = industry deep dive, value-chain teardown, long thesis-driven analysis
    8-12  = company deep dive, sector outlook, thematic note with original analysis
    4-7   = short broker note, single-event commentary, end-of-day recap
    1-3   = roadshow / call / conference / interview transcript, news roundup
    0     = pure news copy, headline list, social-media chatter
    Hard cap: if title contains 录音/纪要/路演/电话会/调研纪要/访谈/对话/快讯/速报, cap this dimension at 5.

- durability (0-15): How fast does the content decay?
    13-15 = industry structure, capacity cycle, technology roadmap, valuation framework (24+ months)
    8-12  = annual/interim report analysis, full-year strategy outlook (12 months)
    4-7   = quarterly earnings note, quarter-ahead outlook (3-6 months)
    1-3   = daily/weekly/monthly digest, end-of-day note, risk-spotter, dawn brief (days)
    0     = same-day news, single hot-topic chase
    Hard cap: if title contains 每日/周报/月报/避雷/收市/盘前/盘后/晨会/晨报/早评/夜报, cap this dimension at 3.

- source_coverage (0-15): Does the summary capture the source's major sections and claims?
    13-15 = preserves all major sections, claims, numbers
    8-12  = covers core claims, drops some peripheral detail
    4-7   = covers headline message only
    0-3   = thin, misses important sections

- factual_density (0-15): Numbers, entities, dates, assumptions, source-backed facts?
    13-15 = many concrete numbers + entities + dates traceable to source
    8-12  = good factual content but uneven
    4-7   = some specifics, mostly prose
    0-3   = vague, qualitative, hand-wavy

- retrieval_value (0-10): Will this help future queries on this KB?
    8-10 = high recall value, indexable claims and concepts
    5-7  = useful but generic
    2-4  = limited; mostly restates source style
    0-1  = unlikely to surface in any future query

- novelty_vs_kb (0-10): How novel is this relative to {EXISTING_CONCEPTS_DIGEST}?
    8-10 = brand-new topic OR material update to existing concept
    5-7  = adds incremental angle to existing pages
    2-4  = mostly already covered in the existing wiki
    0-1  = ≥80% overlap with an existing page

- structure_clarity (0-5): Strict — do NOT default to 5.
    5 = full heading hierarchy + lists/tables + scannable
    3 = readable but uneven structure or long paragraphs
    1 = paragraph dump, no clear sections
    0 = unstructured prose

- actionability (0-5):
    4-5 = preserves decisions, indicators, catalysts, risk triggers, monitoring metrics
    2-3 = some actionable signals but partial
    0-1 = informational only, no actionable hooks

- cross_linking (0-5):
    4-5 = identifies multiple durable [[concepts/...]] worth reusing
    2-3 = 1-2 cross-link candidates
    0-1 = no reusable concept hooks

- topic_fit (0-5): Is this within the KB's scope?
    KB scope: {KB_TOPIC}
    5 = direct investment research with tickers/ratings/forecasts
    3 = investment-adjacent (macro, policy, value chain)
    1 = weak investment angle (general business commentary)
    0 = completely unrelated to investment research

Sum across all 10 dimensions must equal total_score (0-100).
Default toward the lower band when the document is borderline — over-scoring causes KB bloat.
"""

_SUMMARY_SCORE_JSON_SHAPE = """\
"scorecard": an object with:
  - "version": must be the string "v2"
  - "method": short label for the scoring method
  - "overall_assessment": one short paragraph explaining the score, citing weakest dimensions
  - "total_score": integer 0-100 (must equal sum of all dimension scores)
  - "dimensions": object with EXACTLY these keys:
    - "research_depth": {{"score": 0-15, "reason": ""}}
    - "durability": {{"score": 0-15, "reason": ""}}
    - "source_coverage": {{"score": 0-15, "reason": ""}}
    - "factual_density": {{"score": 0-15, "reason": ""}}
    - "retrieval_value": {{"score": 0-10, "reason": ""}}
    - "novelty_vs_kb": {{"score": 0-10, "reason": ""}}
    - "structure_clarity": {{"score": 0-5, "reason": ""}}
    - "actionability": {{"score": 0-5, "reason": ""}}
    - "cross_linking": {{"score": 0-5, "reason": ""}}
    - "topic_fit": {{"score": 0-5, "reason": ""}}\
"""

_SUMMARY_WITH_SCORE_USER = """\
New document: {doc_name}

Full text:
{content}

Write a review-stage summary page for this document in Markdown, and score the
document's value for admission into the knowledge base.

Scoring intent:
- This IS a KB-admission score (not just summary-quality). Score conservatively.
- Penalize documents that are timely-news/transcripts/short notes, even if well-written.
- Penalize content that overlaps heavily with existing concepts.
- Reward durable, deep, original investment research.

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

{scoring_rubric}

Return a JSON object with these keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown
- {scorecard_shape}

Return ONLY valid JSON, no fences.
"""

_LOCAL_LONG_DOC_SUMMARY_WITH_SCORE_USER = """\
This is a page-indexed local extraction for long document "{doc_name}".

{content}

Based on this page-indexed extraction, write a high-signal review-stage summary
page and score the document's value for admission into the knowledge base.

Scoring intent:
- This IS a KB-admission score (not just summary-quality). Score conservatively.
- Penalize documents that are timely-news/transcripts/short notes, even if well-written.
- Penalize content that overlaps heavily with existing concepts.
- Reward durable, deep, original investment research.

For investment research reports, preserve ratings, company names, forecasts,
valuation context, key numbers, catalysts, risks, and monitoring indicators.
Use page references like "p.12" where evidence is available.

{scoring_rubric}

Return a JSON object with these keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown with durable [[concepts/...]] links
- {scorecard_shape}

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


_RESCORE_USER = """\
Rescore the existing review-stage summary for "{doc_name}" against the v2
scoring rubric below. DO NOT rewrite the summary; only return a fresh
scorecard object that scores the summary text as a candidate for KB admission.

Existing summary (already written):
{summary}

{scoring_rubric}

Return a JSON object with exactly these top-level keys:
- {scorecard_shape}

Return ONLY valid JSON, no fences. Do not include any other top-level keys.
"""


def rescore_summary(
    kb_dir: Path,
    selector: str,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Recompute the v2 scorecard for an existing review summary without regenerating it."""
    kb_dir = Path(kb_dir)
    document = resolve_source_document(kb_dir, selector)
    file_hash = str(document["hash"])
    stem = str(document["stem"])

    ledger_record = get_document_ledger_record(kb_dir, file_hash)
    if ledger_record is None:
        ledger_record = build_document_ledger_record(
            file_hash,
            defaults=infer_document_ledger_defaults(document),
        )

    review = ledger_record.get("review", {}) if isinstance(ledger_record.get("review"), dict) else {}
    old_scorecard = review.get("summary_scorecard")
    old_version = ""
    if isinstance(old_scorecard, dict):
        old_version = str(old_scorecard.get("version") or "").strip().lower()
    if old_version == "v2":
        return {
            "file_hash": file_hash,
            "name": document["name"],
            "skipped": True,
            "skip_reason": "already_v2",
            "new_total_score": (old_scorecard.get("total_score") if isinstance(old_scorecard, dict) else None),
        }

    summary_text, _rel = read_review_summary(
        kb_dir,
        stem=stem,
        ingested_at=document.get("ingested_at"),
        review_summary_path=str(ledger_record.get("review_summary_path") or ""),
    )
    summary_body = _strip_summary_frontmatter(summary_text)
    selected_model = model or _default_model(kb_dir)

    new_scorecard = _rescore_summary_text(kb_dir, doc_name=document["name"], summary=summary_body, model=selected_model)

    updates: dict[str, Any] = {
        "review": {
            "summary_score": new_scorecard.get("total_score"),
            "summary_score_source": "auto_rescore_v2",
            "summary_scorecard": new_scorecard,
            "scorecard_version": str(new_scorecard.get("version") or _SUMMARY_SCORECARD_VERSION),
        },
        "execution": {
            "last_error": "",
            "updated_at": _now_iso(),
        },
    }
    if isinstance(old_scorecard, dict) and old_scorecard:
        updates["review"]["summary_scorecard_v1"] = old_scorecard

    upsert_document_ledger_record(kb_dir, file_hash, updates)

    return {
        "file_hash": file_hash,
        "name": document["name"],
        "skipped": False,
        "new_total_score": new_scorecard.get("total_score"),
        "old_total_score": old_scorecard.get("total_score") if isinstance(old_scorecard, dict) else None,
        "scorecard_version": new_scorecard.get("version"),
    }


def rescore_summaries(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None = None,
    model: str | None = None,
    only_unreviewed: bool = True,
    progress_callback: SummaryProgressCallback | None = None,
) -> dict[str, Any]:
    """Batch rescore existing review summaries with the v2 rubric.

    When ``only_unreviewed`` is True, restrict to documents in review_state
    "unreviewed" with a written review summary. When False, rescore every
    document that has a non-v2 scorecard.
    """
    selected = _selected_rescore_hashes(kb_dir, file_hashes=file_hashes, only_unreviewed=only_unreviewed)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    rescored = 0
    skipped = 0
    total = len(selected)
    completed = 0

    def emit(event: str, **payload: Any) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "completed": completed, "total": total, **payload})

    emit("selected")
    for index, file_hash in enumerate(selected, 1):
        emit("start", index=index, file_hash=file_hash)
        try:
            result = rescore_summary(kb_dir, file_hash, model=model)
        except Exception as exc:
            completed += 1
            failures.append({"file_hash": file_hash, "error": str(exc)})
            emit("failure", index=index, file_hash=file_hash, error=str(exc))
            continue
        completed += 1
        results.append(result)
        if result.get("skipped"):
            skipped += 1
            emit("skipped", index=index, file_hash=file_hash, reason=result.get("skip_reason"))
        else:
            rescored += 1
            emit(
                "rescored",
                index=index,
                file_hash=file_hash,
                old=result.get("old_total_score"),
                new=result.get("new_total_score"),
            )
    return {
        "rescored": rescored,
        "skipped": skipped,
        "failed": len(failures),
        "total": total,
        "failures": failures,
        "documents": results,
    }


def _selected_rescore_hashes(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None,
    only_unreviewed: bool,
) -> list[str]:
    if file_hashes is not None:
        return [str(h).strip() for h in file_hashes if str(h).strip()]
    filters: dict[str, Any] = {"summary_state": "ready"}
    if only_unreviewed:
        filters["review_state"] = "unreviewed"
    return [
        record["file_hash"]
        for record in select_document_ledger_records(kb_dir, **filters)
    ]


def _rescore_summary_text(kb_dir: Path, *, doc_name: str, summary: str, model: str) -> dict[str, Any]:
    wiki_dir = kb_dir / "wiki"
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language = str(config.get("language") or DEFAULT_CONFIG["language"])
    kb_topic = _kb_topic_for_scoring(config, wiki_dir)
    existing_concepts = _existing_concepts_digest(wiki_dir)
    scoring_rubric = _render_scoring_rubric(kb_topic=kb_topic, existing_concepts=existing_concepts)
    system_msg = {
        "role": "system",
        "content": _SYSTEM_TEMPLATE.format(schema_md=get_agents_md(wiki_dir), language=language),
    }
    user_msg = {
        "role": "user",
        "content": _RESCORE_USER.format(
            doc_name=doc_name,
            summary=summary,
            scoring_rubric=scoring_rubric,
            scorecard_shape=_SUMMARY_SCORE_JSON_SHAPE,
        ),
    }
    raw = _llm_call(model, [system_msg, user_msg], "rescore-summary")
    try:
        parsed = _parse_json(raw)
        if isinstance(parsed, dict):
            payload = parsed.get("scorecard") if isinstance(parsed.get("scorecard"), dict) else parsed
            return _normalize_summary_scorecard(payload, summary)
    except (json.JSONDecodeError, ValueError):
        pass
    return _fallback_summary_scorecard(summary)


def _kb_topic_for_scoring(config: dict, wiki_dir: Path) -> str:
    """Return the KB topic description used to anchor topic_fit scoring."""
    cfg_topic = ""
    auto_cfg = config.get("auto_review") if isinstance(config.get("auto_review"), dict) else {}
    if isinstance(auto_cfg.get("kb_topic"), str):
        cfg_topic = str(auto_cfg.get("kb_topic")).strip()
    if cfg_topic:
        return cfg_topic
    return (
        "Cross-market (A-shares / HK / US) investment research — companies, "
        "industries, themes, risks, metrics, and value-chain structure."
    )


def _existing_concepts_digest(wiki_dir: Path, *, limit: int = 30) -> str:
    """Return a short digest of existing concept page names for novelty scoring."""
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return "(no existing concept pages yet)"
    try:
        stems = sorted(
            (p.stem for p in concepts_dir.glob("*.md") if p.is_file()),
            key=str.lower,
        )
    except OSError:
        return "(unable to enumerate existing concept pages)"
    if not stems:
        return "(no existing concept pages yet)"
    head = stems[:limit]
    suffix = f" ... and {len(stems) - len(head)} more" if len(stems) > len(head) else ""
    return ", ".join(head) + suffix


def _render_scoring_rubric(*, kb_topic: str, existing_concepts: str) -> str:
    return _SUMMARY_SCORE_RUBRIC.format(
        KB_TOPIC=kb_topic,
        EXISTING_CONCEPTS_DIGEST=existing_concepts,
    )


def _generate_summary_only(kb_dir: Path, doc_name: str, source_path: Path, model: str) -> dict[str, Any]:
    wiki_dir = kb_dir / "wiki"
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language = str(config.get("language") or DEFAULT_CONFIG["language"])
    kb_topic = _kb_topic_for_scoring(config, wiki_dir)
    existing_concepts = _existing_concepts_digest(wiki_dir)
    scoring_rubric = _render_scoring_rubric(kb_topic=kb_topic, existing_concepts=existing_concepts)
    system_msg = {
        "role": "system",
        "content": _SYSTEM_TEMPLATE.format(schema_md=get_agents_md(wiki_dir), language=language),
    }
    if source_path.suffix.lower() == ".json":
        content = _build_local_long_doc_context(source_path)
        doc_msg = {
            "role": "user",
            "content": _LOCAL_LONG_DOC_SUMMARY_WITH_SCORE_USER.format(
                doc_name=doc_name,
                content=content,
                scoring_rubric=scoring_rubric,
                scorecard_shape=_SUMMARY_SCORE_JSON_SHAPE,
            ),
        }
        step_name = "local-long-summary"
    else:
        content = source_path.read_text(encoding="utf-8")
        doc_msg = {
            "role": "user",
            "content": _SUMMARY_WITH_SCORE_USER.format(
                doc_name=doc_name,
                content=content,
                scoring_rubric=scoring_rubric,
                scorecard_shape=_SUMMARY_SCORE_JSON_SHAPE,
            ),
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

    # Detect whether the LLM returned a v1 scorecard (old 6-dim shape) and adapt.
    version_raw = str(raw.get("version") or "").strip().lower()
    is_v1_payload = version_raw == "v1" or _looks_like_v1_scorecard(dimensions_raw)
    schema = _LEGACY_SUMMARY_SCORE_DIMENSIONS_V1 if is_v1_payload else _SUMMARY_SCORE_DIMENSIONS

    dimensions: dict[str, dict[str, Any]] = {}
    total = 0
    for name, label, max_score in schema:
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
    if is_v1_payload:
        default_method = "llm_summary_value_v1"
        version = "v1"
    else:
        default_method = "llm_summary_value_v2"
        version = _SUMMARY_SCORECARD_VERSION
    method = str(raw.get("method") or "").strip() or default_method
    if not overall:
        overall = fallback["overall_assessment"]
    return {
        "version": version,
        "method": method,
        "overall_assessment": overall,
        "total_score": total if stated_total != total else stated_total,
        "dimensions": dimensions,
    }


def _looks_like_v1_scorecard(dimensions_raw: dict) -> bool:
    """Return True when the LLM payload matches the legacy v1 dimension shape.

    We treat it as v1 only when NONE of the v2-only keys (research_depth,
    durability, novelty_vs_kb, topic_fit) are present AND at least one legacy
    key is present. This keeps backward compatibility for older test fixtures
    and ledger entries.
    """
    if not isinstance(dimensions_raw, dict):
        return False
    v2_only = {"research_depth", "durability", "novelty_vs_kb", "topic_fit"}
    if v2_only & set(dimensions_raw.keys()):
        return False
    legacy_only_max = {
        # Score values that would only fit under v1 max ranges (>= v2 cap + 1)
        "source_coverage": _SUMMARY_SCORE_DIMENSION_MAX["source_coverage"],
        "factual_density": _SUMMARY_SCORE_DIMENSION_MAX["factual_density"],
        "structure_clarity": _SUMMARY_SCORE_DIMENSION_MAX["structure_clarity"],
        "retrieval_value": _SUMMARY_SCORE_DIMENSION_MAX["retrieval_value"],
        "actionability": _SUMMARY_SCORE_DIMENSION_MAX["actionability"],
        "cross_linking": _SUMMARY_SCORE_DIMENSION_MAX["cross_linking"],
    }
    has_legacy_score = False
    for key, v2_cap in legacy_only_max.items():
        raw_dim = dimensions_raw.get(key)
        if not isinstance(raw_dim, dict):
            continue
        try:
            score = int(raw_dim.get("score", 0))
        except (TypeError, ValueError):
            continue
        if score > v2_cap:
            return True
        if key in dimensions_raw:
            has_legacy_score = True
    # Fallback heuristic: only the 6 legacy keys are present and no v2 keys
    legacy_keys = set(legacy_only_max.keys())
    present = set(dimensions_raw.keys()) & legacy_keys
    return has_legacy_score and present == set(dimensions_raw.keys()) and len(present) >= 4


def _fallback_summary_scorecard(summary: str) -> dict[str, Any]:
    text = str(summary or "")
    line_count = len([line for line in text.splitlines() if line.strip()])
    bullet_count = text.count("\n- ") + text.count("\n* ")
    wiki_link_count = text.count("[[")
    concept_link_count = text.count("[[concepts/")
    digit_count = sum(char.isdigit() for char in text)
    heading_count = sum(1 for line in text.splitlines() if line.strip().startswith("#"))
    sentences = [part.strip() for part in text.replace("\n", " ").split("。")]
    sentences = [part for part in sentences if part]

    dimensions = {
        "research_depth": {
            "label": "Research Depth",
            "score": min(15, 4 + min(heading_count, 5) + min(line_count, 30) // 6),
            "max": 15,
            "reason": "Heuristic estimate based on summary length and structure depth.",
        },
        "durability": {
            "label": "Durability",
            "score": min(15, 6 + min(concept_link_count, 5)),
            "max": 15,
            "reason": "Heuristic estimate — assumes neutral durability without title signals.",
        },
        "source_coverage": {
            "label": "Source Coverage",
            "score": min(15, 5 + min(line_count, 24) // 3 + min(heading_count, 3)),
            "max": 15,
            "reason": "Heuristic estimate based on summary breadth, sectioning, and coverage signals.",
        },
        "factual_density": {
            "label": "Factual Density",
            "score": min(15, 3 + min(digit_count, 36) // 4),
            "max": 15,
            "reason": "Heuristic estimate based on preserved numeric and concrete source-backed detail.",
        },
        "retrieval_value": {
            "label": "Retrieval Value",
            "score": min(10, 3 + min(line_count, 18) // 3 + min(len(sentences), 6) // 3),
            "max": 10,
            "reason": "Heuristic estimate of how useful the summary will be for later recall and querying.",
        },
        "novelty_vs_kb": {
            "label": "Novelty vs KB",
            "score": min(10, 5),  # Neutral fallback — overlap not measurable from text alone.
            "max": 10,
            "reason": "Heuristic neutral score; deterministic overlap is computed by auto_review_overlap.",
        },
        "structure_clarity": {
            "label": "Structure & Clarity",
            "score": min(5, 1 + min(heading_count, 3) + min(bullet_count, 6) // 3),
            "max": 5,
            "reason": "Heuristic estimate based on headings, list structure, and scanability.",
        },
        "actionability": {
            "label": "Actionability",
            "score": min(5, 1 + min(bullet_count, 8) // 3 + (1 if digit_count >= 6 else 0)),
            "max": 5,
            "reason": "Heuristic estimate based on whether the summary likely preserves indicators, risks, or decisions.",
        },
        "cross_linking": {
            "label": "Cross-linking",
            "score": min(5, min(wiki_link_count, 5)),
            "max": 5,
            "reason": "Heuristic estimate based on reusable concept linking signals in the summary.",
        },
        "topic_fit": {
            "label": "Topic Fit",
            "score": min(5, 3),  # Neutral default; topic mismatch is rare and not detectable from text alone.
            "max": 5,
            "reason": "Heuristic neutral score; topic fit requires KB-context awareness.",
        },
    }
    total = sum(int(item["score"]) for item in dimensions.values())
    return {
        "version": _SUMMARY_SCORECARD_VERSION,
        "method": "heuristic_summary_value_v2",
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
