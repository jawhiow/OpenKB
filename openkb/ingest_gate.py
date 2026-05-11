"""Pre-ingest scoring gate for OpenKB document imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any

import pymupdf
from json_repair import repair_json
from markitdown import MarkItDown

from openkb.llm_runtime import completion


INGEST_GATE_HISTORY = "ingest_gate_history.jsonl"
_INDEX_ENTRY_RE = re.compile(r"^- \[\[([^\]]+)\]\](?:\s+\([^)]+\))?\s+-\s+(.*)$")

_DEFAULT_DIMENSION_MAX = {
    "relevance": 15,
    "authority_traceability": 15,
    "signal_density": 20,
    "novelty_vs_kb": 20,
    "durability": 10,
    "compilation_yield": 10,
    "actionability": 10,
}

_SYSTEM_PROMPT = """You are OpenKB's pre-ingest scoring gate for an investment research knowledge base.

Your task is not to summarize the document. Your task is to decide whether the document is worth admitting into the knowledge base before it is copied into raw/ and compiled.

Core philosophy:
1. Every raw document increases future compilation, maintenance, de-duplication, and query cost.
2. Low-quality inputs pollute the wiki with weak summaries and duplicate concepts.
3. Admission should be conservative. Prefer high-signal, high-traceability, durable knowledge.
4. Think in the style of a high-taste LLM wiki curator: fewer, better documents.

Scoring rubric (100 total):
- relevance: 0-15
- authority_traceability: 0-15
- signal_density: 0-20
- novelty_vs_kb: 0-20
- durability: 0-10
- compilation_yield: 0-10
- actionability: 0-10

You must output JSON only.
Every dimension must include:
- score
- reason
- evidence (array of short bullet strings)
- deductions (array of short bullet strings)

If the document has unreadable OCR, unclear provenance, or is highly duplicative with no real added value, set hard_reject=true.
Be conservative.
Write reasons in the requested language.
"""

_USER_PROMPT = """Evaluate this candidate document for pre-ingest admission.

[Document title]
{doc_title}

[Guessed document type]
{doc_type}

[Source info]
{source_info}

[Document length]
{doc_length}

[KB language]
{language}

[Excerpt]
{excerpt}

[Potentially related existing KB pages]
{related_pages}

Scoring thresholds:
- pass_threshold: {pass_threshold}
- hold_threshold: {hold_threshold}

Return JSON with this exact top-level structure:
{{
  "doc_title": "",
  "doc_type": "",
  "hard_reject": false,
  "dimension_scores": {{
    "relevance": {{"score": 0, "max": 15, "reason": "", "evidence": [], "deductions": []}},
    "authority_traceability": {{"score": 0, "max": 15, "reason": "", "evidence": [], "deductions": []}},
    "signal_density": {{"score": 0, "max": 20, "reason": "", "evidence": [], "deductions": []}},
    "novelty_vs_kb": {{"score": 0, "max": 20, "reason": "", "evidence": [], "deductions": []}},
    "durability": {{"score": 0, "max": 10, "reason": "", "evidence": [], "deductions": []}},
    "compilation_yield": {{"score": 0, "max": 10, "reason": "", "evidence": [], "deductions": []}},
    "actionability": {{"score": 0, "max": 10, "reason": "", "evidence": [], "deductions": []}}
  }},
  "primary_reasons": [],
  "hard_reject_reasons": [],
  "overlap_with_existing_kb": [],
  "suggested_outputs_if_ingested": [],
  "one_line_verdict": "",
  "recommended_ingest_mode": "full_ingest | summary_only | concept_extraction_only | manual_review | reject",
  "audit_trail": {{
    "why_this_decision": "",
    "why_not_higher": "",
    "why_not_lower": ""
  }}
}}

Do not include markdown fences.
"""


@dataclass(frozen=True)
class IngestGateConfig:
    enabled: bool
    pass_threshold: int
    hold_threshold: int
    hard_reject_enabled: bool
    log_all_decisions: bool
    allow_force_pass: bool
    allow_force_reject: bool


def ingest_gate_config(config: dict[str, Any]) -> IngestGateConfig:
    gate = config.get("ingest_gate") if isinstance(config.get("ingest_gate"), dict) else {}
    return IngestGateConfig(
        enabled=bool(gate.get("enabled", False)),
        pass_threshold=max(int(gate.get("pass_threshold", 75)), 0),
        hold_threshold=max(int(gate.get("hold_threshold", 60)), 0),
        hard_reject_enabled=bool(gate.get("hard_reject_enabled", True)),
        log_all_decisions=bool(gate.get("log_all_decisions", True)),
        allow_force_pass=bool(gate.get("allow_force_pass", True)),
        allow_force_reject=bool(gate.get("allow_force_reject", True)),
    )


def gate_is_active(config: IngestGateConfig, *, force_pass: bool = False, force_reject: bool = False) -> bool:
    return config.enabled or force_pass or force_reject


def evaluate_candidate(
    file_path: Path,
    kb_dir: Path,
    *,
    model: str,
    language: str,
    config: IngestGateConfig,
    force_pass: bool = False,
    force_reject: bool = False,
    force_reason: str = "",
    operator: str = "",
) -> dict[str, Any]:
    doc_title = file_path.name
    doc_type = _infer_doc_type(file_path)
    source_info = f"file: {file_path}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    excerpt_result = _extract_excerpt(file_path)
    if excerpt_result["error"]:
        raw = _empty_result(doc_title, doc_type, config)
        raw["hard_reject"] = True
        raw["hard_reject_reasons"] = [excerpt_result["error"]]
        raw["primary_reasons"] = [excerpt_result["error"]]
        raw["one_line_verdict"] = "Document preview extraction failed; rejecting conservatively."
        raw["recommended_ingest_mode"] = "reject"
        raw["audit_trail"] = {
            "why_this_decision": excerpt_result["error"],
            "why_not_higher": "The document could not be previewed reliably before ingestion.",
            "why_not_lower": "The failure itself is sufficient for a conservative reject.",
        }
        return _finalize_result(
            raw,
            config=config,
            force_pass=force_pass,
            force_reject=force_reject,
            force_reason=force_reason,
            operator=operator,
            source_info=source_info,
            doc_length=excerpt_result["doc_length"],
            timestamp=timestamp,
        )

    related_pages = _related_pages(kb_dir, doc_title, excerpt_result["excerpt"])
    prompt = _USER_PROMPT.format(
        doc_title=doc_title,
        doc_type=doc_type,
        source_info=source_info,
        doc_length=excerpt_result["doc_length"],
        language=language,
        excerpt=excerpt_result["excerpt"],
        related_pages="\n".join(f"- {item}" for item in related_pages) if related_pages else "- (none)",
        pass_threshold=config.pass_threshold,
        hold_threshold=config.hold_threshold,
    )
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    parsed = _normalize_llm_result(_parse_json_response(response.text), doc_title, doc_type)
    return _finalize_result(
        parsed,
        config=config,
        force_pass=force_pass,
        force_reject=force_reject,
        force_reason=force_reason,
        operator=operator,
        source_info=source_info,
        doc_length=excerpt_result["doc_length"],
        timestamp=timestamp,
    )


def record_gate_decision(kb_dir: Path, result: dict[str, Any], *, language: str) -> None:
    _append_history_jsonl(kb_dir, result)
    page_path, page_id = _log_page_path(kb_dir, language)
    _ensure_log_page(page_path, language)
    _append_markdown_record(page_path, result, language=language)
    _ensure_index_entry(kb_dir / "wiki" / "index.md", page_id, _index_brief(language))


def should_continue_ingest(result: dict[str, Any]) -> bool:
    return result.get("final_decision") in {"PASS", "FORCE_PASS"}


def gate_summary_line(result: dict[str, Any]) -> str:
    score = result.get("total_score")
    score_part = "unscored" if score is None else f"{score}/100"
    return f"{result.get('final_decision', 'UNKNOWN')} {score_part}"


def _empty_result(doc_title: str, doc_type: str, config: IngestGateConfig) -> dict[str, Any]:
    return {
        "doc_title": doc_title,
        "doc_type": doc_type,
        "hard_reject": False,
        "dimension_scores": {
            key: {"score": 0, "max": max_score, "reason": "", "evidence": [], "deductions": []}
            for key, max_score in _DEFAULT_DIMENSION_MAX.items()
        },
        "primary_reasons": [],
        "hard_reject_reasons": [],
        "overlap_with_existing_kb": [],
        "suggested_outputs_if_ingested": [],
        "one_line_verdict": "",
        "recommended_ingest_mode": "manual_review",
        "audit_trail": {"why_this_decision": "", "why_not_higher": "", "why_not_lower": ""},
        "threshold": config.pass_threshold,
    }


def _finalize_result(
    raw: dict[str, Any],
    *,
    config: IngestGateConfig,
    force_pass: bool,
    force_reject: bool,
    force_reason: str,
    operator: str,
    source_info: str,
    doc_length: str,
    timestamp: str,
) -> dict[str, Any]:
    result = _empty_result(str(raw.get("doc_title") or ""), str(raw.get("doc_type") or "other"), config)
    result.update(raw)
    result["total_score"] = _total_score(result.get("dimension_scores", {}))
    result["threshold"] = config.pass_threshold
    result["hold_threshold"] = config.hold_threshold
    result["gate_enabled"] = config.enabled
    result["force_pass"] = bool(force_pass)
    result["force_reject"] = bool(force_reject)
    result["force_reason"] = force_reason.strip()
    result["operator"] = operator or os.getenv("USER", "")
    result["source_info"] = source_info
    result["doc_length"] = doc_length
    result["timestamp"] = timestamp

    raw_decision = _raw_decision(result["total_score"], config)
    if result.get("hard_reject") and config.hard_reject_enabled:
        raw_decision = "REJECT"
    result["raw_decision"] = raw_decision

    if force_reject:
        final_decision = "FORCE_REJECT"
    elif force_pass:
        final_decision = "FORCE_PASS"
    else:
        final_decision = raw_decision
    result["final_decision"] = final_decision

    if not result.get("one_line_verdict"):
        result["one_line_verdict"] = _default_verdict(final_decision, result["total_score"])

    if final_decision in {"REJECT", "FORCE_REJECT"}:
        result["recommended_ingest_mode"] = "reject"
    elif final_decision == "HOLD":
        result["recommended_ingest_mode"] = "manual_review"
    elif final_decision in {"PASS", "FORCE_PASS"} and result.get("recommended_ingest_mode") == "reject":
        result["recommended_ingest_mode"] = "full_ingest"

    return result


def _raw_decision(total_score: int, config: IngestGateConfig) -> str:
    if total_score >= config.pass_threshold:
        return "PASS"
    if total_score >= config.hold_threshold:
        return "HOLD"
    return "REJECT"


def _default_verdict(final_decision: str, total_score: int | None) -> str:
    score = "unscored" if total_score is None else f"{total_score}/100"
    return f"{final_decision}: {score}"


def _total_score(dimension_scores: dict[str, Any]) -> int:
    total = 0
    for key, max_score in _DEFAULT_DIMENSION_MAX.items():
        value = dimension_scores.get(key) if isinstance(dimension_scores, dict) else {}
        try:
            score = int(value.get("score", 0))
        except Exception:
            score = 0
        total += max(0, min(score, max_score))
    return total


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1 :] if first_nl != -1 else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    parsed = json.loads(repair_json(cleaned.strip()))
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object from ingest gate response.")
    return parsed


def _normalize_llm_result(parsed: dict[str, Any], doc_title: str, doc_type: str) -> dict[str, Any]:
    result = dict(parsed)
    result["doc_title"] = str(parsed.get("doc_title") or doc_title)
    result["doc_type"] = str(parsed.get("doc_type") or doc_type)
    dimension_scores = parsed.get("dimension_scores") if isinstance(parsed.get("dimension_scores"), dict) else {}
    normalized_scores: dict[str, Any] = {}
    for key, max_score in _DEFAULT_DIMENSION_MAX.items():
        raw = dimension_scores.get(key) if isinstance(dimension_scores.get(key), dict) else {}
        try:
            score = int(raw.get("score", 0))
        except Exception:
            score = 0
        normalized_scores[key] = {
            "score": max(0, min(score, max_score)),
            "max": max_score,
            "reason": str(raw.get("reason") or "").strip(),
            "evidence": [str(item).strip() for item in raw.get("evidence", []) if str(item).strip()] if isinstance(raw.get("evidence"), list) else [],
            "deductions": [str(item).strip() for item in raw.get("deductions", []) if str(item).strip()] if isinstance(raw.get("deductions"), list) else [],
        }
    result["dimension_scores"] = normalized_scores
    for key in ("primary_reasons", "hard_reject_reasons", "overlap_with_existing_kb", "suggested_outputs_if_ingested"):
        value = parsed.get(key)
        result[key] = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
    audit_trail = parsed.get("audit_trail") if isinstance(parsed.get("audit_trail"), dict) else {}
    result["audit_trail"] = {
        "why_this_decision": str(audit_trail.get("why_this_decision") or "").strip(),
        "why_not_higher": str(audit_trail.get("why_not_higher") or "").strip(),
        "why_not_lower": str(audit_trail.get("why_not_lower") or "").strip(),
    }
    result["hard_reject"] = bool(parsed.get("hard_reject", False))
    result["one_line_verdict"] = str(parsed.get("one_line_verdict") or "").strip()
    result["recommended_ingest_mode"] = str(parsed.get("recommended_ingest_mode") or "manual_review").strip()
    return result


def _infer_doc_type(file_path: Path) -> str:
    stem = file_path.stem.lower()
    if "z-library" in stem or "书" in stem or "handbook" in stem or "manual" in stem:
        return "book"
    if "年报" in stem or "annual" in stem:
        return "annual_report"
    if "季报" in stem or "财报" in stem or "earnings" in stem:
        return "financial_report"
    if any(token in stem for token in ("报告", "覆盖", "周报", "月报", "专题", "点评", "调研", "深度", "证券")):
        return "research_report"
    if any(token in stem for token in ("讲义", "课程", ".mp4", "lecture", "notes")):
        return "lecture_notes"
    return "other"


def _extract_excerpt(file_path: Path, max_chars: int = 6000, max_pdf_pages: int = 3) -> dict[str, str]:
    try:
        if file_path.suffix.lower() == ".pdf":
            with pymupdf.open(str(file_path)) as doc:
                page_count = doc.page_count
                chunks: list[str] = []
                for index in range(min(page_count, max_pdf_pages)):
                    text = doc.load_page(index).get_text("text")
                    if text:
                        chunks.append(text.strip())
                excerpt = "\n\n".join(chunk for chunk in chunks if chunk).strip()
            return {
                "excerpt": excerpt[:max_chars] if excerpt else "",
                "doc_length": f"{page_count} pages",
                "error": "PDF preview extraction returned no readable text." if not excerpt else "",
            }

        converted = MarkItDown().convert(str(file_path))
        text = getattr(converted, "text_content", "") or ""
        line_count = len(text.splitlines()) if text else 0
        return {
            "excerpt": text[:max_chars].strip(),
            "doc_length": f"{line_count} lines",
            "error": "Document preview extraction returned no readable text." if not text.strip() else "",
        }
    except Exception as exc:
        return {"excerpt": "", "doc_length": "", "error": f"Preview extraction failed: {exc}"}


def _related_pages(kb_dir: Path, doc_title: str, excerpt: str, limit: int = 8) -> list[str]:
    index_path = kb_dir / "wiki" / "index.md"
    if not index_path.exists():
        return []
    query_terms = _semantic_terms(doc_title + "\n" + excerpt[:2000])
    candidates: list[tuple[float, str]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = _INDEX_ENTRY_RE.match(line.strip())
        if not match:
            continue
        page_id = match.group(1).strip()
        brief = match.group(2).strip()
        candidate_text = f"{page_id}\n{brief}"
        similarity = _text_similarity(query_terms, _semantic_terms(candidate_text))
        if similarity <= 0:
            continue
        candidates.append((similarity, f"[[{page_id}]] - {brief}"))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:limit]]


def _semantic_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(block) <= 2:
            terms.add(block)
        else:
            terms.update(block[i : i + 2] for i in range(len(block) - 1))
            terms.update(block[i : i + 3] for i in range(len(block) - 2))
    for token in re.findall(r"[a-z0-9][a-z0-9_.%+-]*", text.lower()):
        if len(token) >= 3:
            terms.add(token)
    return terms


def _text_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _append_history_jsonl(kb_dir: Path, result: dict[str, Any]) -> None:
    history_path = kb_dir / ".openkb" / INGEST_GATE_HISTORY
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


def _log_page_path(kb_dir: Path, language: str) -> tuple[Path, str]:
    if str(language).lower().startswith("zh"):
        rel = "explorations/资料准入评分台账.md"
    else:
        rel = "explorations/ingest_gate.md"
    return kb_dir / "wiki" / rel, rel[:-3]


def _ensure_log_page(path: Path, language: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(language).lower().startswith("zh"):
        title = "# 资料准入评分台账\n\n用于记录入库前评分门禁的所有结果。\n\n"
    else:
        title = "# Ingest Gate Audit Log\n\nRecords pre-ingest gate decisions for candidate documents.\n\n"
    path.write_text(title, encoding="utf-8")


def _append_markdown_record(path: Path, result: dict[str, Any], *, language: str) -> None:
    lines = [
        f"## [{result.get('timestamp', '')}] {result.get('doc_title', '')} | {result.get('final_decision', '')} {result.get('total_score', 'unscored')}/100",
        f"- Type: {result.get('doc_type', '')}",
        f"- Source: {result.get('source_info', '')}",
        f"- Gate enabled: {result.get('gate_enabled', False)}",
        f"- Raw decision: {result.get('raw_decision', '')}",
        f"- Force pass: {result.get('force_pass', False)}",
        f"- Force reject: {result.get('force_reject', False)}",
        f"- Force reason: {result.get('force_reason', '')}",
        f"- Operator: {result.get('operator', '')}",
        f"- Hard reject: {result.get('hard_reject', False)}",
        f"- Recommended ingest mode: {result.get('recommended_ingest_mode', '')}",
        f"- Verdict: {result.get('one_line_verdict', '')}",
        "- Dimension scores:",
    ]
    for key, label in (
        ("relevance", "Relevance"),
        ("authority_traceability", "Authority/Traceability"),
        ("signal_density", "Signal Density"),
        ("novelty_vs_kb", "Novelty vs KB"),
        ("durability", "Durability"),
        ("compilation_yield", "Compilation Yield"),
        ("actionability", "Actionability"),
    ):
        item = result.get("dimension_scores", {}).get(key, {})
        lines.append(f"  - {label}: {item.get('score', 0)}/{item.get('max', _DEFAULT_DIMENSION_MAX[key])} — {item.get('reason', '')}")
    if result.get("primary_reasons"):
        lines.append("- Primary reasons:")
        lines.extend(f"  - {item}" for item in result["primary_reasons"])
    if result.get("hard_reject_reasons"):
        lines.append("- Hard reject reasons:")
        lines.extend(f"  - {item}" for item in result["hard_reject_reasons"])
    if result.get("suggested_outputs_if_ingested"):
        lines.append("- Suggested outputs if ingested:")
        lines.extend(f"  - {item}" for item in result["suggested_outputs_if_ingested"])
    if result.get("overlap_with_existing_kb"):
        lines.append("- Overlap with existing KB:")
        lines.extend(f"  - {item}" for item in result["overlap_with_existing_kb"])
    audit = result.get("audit_trail", {})
    lines.append("- Audit trail:")
    lines.append(f"  - Why this decision: {audit.get('why_this_decision', '')}")
    lines.append(f"  - Why not higher: {audit.get('why_not_higher', '')}")
    lines.append(f"  - Why not lower: {audit.get('why_not_lower', '')}")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n\n")


def _ensure_index_entry(index_path: Path, page_id: str, brief: str) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n## Documents\n\n## Companies\n\n## Industries\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
    lines = index_path.read_text(encoding="utf-8").splitlines()
    if any(line.startswith(f"- [[{page_id}]]") for line in lines):
        return
    try:
        section_index = lines.index("## Explorations")
    except ValueError:
        lines.extend(["", "## Explorations"])
        section_index = len(lines) - 1
    insert_at = section_index + 1
    while insert_at < len(lines) and lines[insert_at].startswith("- [["):
        insert_at += 1
    lines.insert(insert_at, f"- [[{page_id}]] - {brief}")
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _index_brief(language: str) -> str:
    if str(language).lower().startswith("zh"):
        return "记录资料入库前评分门禁的通过、拒绝、强制放行与强制拒绝结果。"
    return "Records pre-ingest gate decisions, overrides, and audit details."
