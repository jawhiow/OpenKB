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
    ("research_depth", "研究深度", 15),
    ("durability", "耐久性", 15),
    ("source_coverage", "来源覆盖", 15),
    ("factual_density", "事实密度", 15),
    ("retrieval_value", "检索价值", 10),
    ("novelty_vs_kb", "知识库新颖度", 10),
    ("structure_clarity", "结构与清晰度", 5),
    ("actionability", "可操作性", 5),
    ("cross_linking", "交叉链接", 5),
    ("topic_fit", "主题契合度", 5),
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
评分标准 (v2)。每个维度有明确锚点——不要默认给高分。
仔细阅读锚点，选择最匹配文档的分数段。诚实面对弱点。

- research_depth (0-15)：这是研究性文档还是新闻/纪要片段？
    13-15 = 行业深度报告、产业链拆解、长篇论点驱动分析
    8-12  = 公司深度报告、行业展望、含原创分析的主题笔记
    4-7   = 短篇券商观点、单事件点评、收盘回顾
    1-3   = 路演/电话会/调研纪要/访谈/对话/快讯/速报
    0     = 纯新闻通稿、标题列表、社交媒体讨论
    硬上限：标题包含 录音/纪要/路演/电话会/调研纪要/访谈/对话/快讯/速报 时，本维度上限为5。

- durability (0-15)：内容衰减速度如何？
    13-15 = 行业结构、产能周期、技术路线图、估值框架（24个月+）
    8-12  = 年报/中报分析、全年策略展望（12个月）
    4-7   = 季度业绩点评、季度展望（3-6个月）
    1-3   = 日报/周报/月报、收盘笔记、风险提示、晨会纪要（数天）
    0     = 当日新闻、单热点追踪
    硬上限：标题包含 每日/周报/月报/避雷/收市/盘前/盘后/晨会/晨报/早评/夜报 时，本维度上限为3。

- source_coverage (0-15)：摘要是否覆盖了原文主要章节和核心观点？
    13-15 = 保留所有主要章节、观点和数字
    8-12  = 覆盖核心观点，省略部分细节
    4-7   = 仅覆盖标题信息
    0-3   = 薄弱，遗漏重要章节

- factual_density (0-15)：数字、实体、日期、假设、来源支撑的事实？
    13-15 = 大量具体数字+实体+日期，可追溯来源
    8-12  = 事实内容较好但不均匀
    4-7   = 有部分具体信息，以叙述为主
    0-3   = 模糊、定性、空泛

- retrieval_value (0-10)：对未来查询本知识库有帮助吗？
    8-10 = 高召回价值，可索引的观点和概念
    5-7  = 有用但偏通用
    2-4  = 有限；主要复述原文风格
    0-1  = 不太可能被任何未来查询命中

- novelty_vs_kb (0-10)：相对于{EXISTING_CONCEPTS_DIGEST}有多新颖？
    8-10 = 全新主题 或 对已有概念的重大更新
    5-7  = 为已有页面增加增量角度
    2-4  = 大部分已在现有wiki中覆盖
    0-1  = ≥80%与已有页面重叠

- structure_clarity (0-5)：严格——不要默认给5。
    5 = 完整标题层级+列表/表格+可扫描
    3 = 可读但结构不均或长段落
    1 = 段落堆砌，无清晰章节
    0 = 非结构化叙述

- actionability (0-5)：
    4-5 = 保留决策、指标、催化剂、风险触发器、监控指标
    2-3 = 有部分可操作信号但不完整
    0-1 = 仅信息性内容，无可操作锚点

- cross_linking (0-5)：
    4-5 = 识别多个值得复用的持久[[concepts/...]]
    2-3 = 1-2个交叉链接候选
    0-1 = 无可复用概念锚点

- topic_fit (0-5)：是否在知识库范围内？
    知识库范围：{KB_TOPIC}
    5 = 直接投资研究，含股票代码/评级/预测
    3 = 投资相关（宏观、政策、产业链）
    1 = 投资角度较弱（一般商业评论）
    0 = 完全与投资研究无关

所有10个维度之和必须等于total_score (0-100)。
边界情况下倾向较低分数段——过高评分会导致知识库膨胀。
"""

_SUMMARY_SCORE_JSON_SHAPE = """\
"scorecard": an object with:
  - "version": must be the string "v2"
  - "method": short label for the scoring method
  - "overall_assessment": one short paragraph in Chinese explaining the score, citing weakest dimensions
  - "total_score": integer 0-100 (must equal sum of all dimension scores)
  - "dimensions": object with EXACTLY these keys:
    - "research_depth": {{"score": 0-15, "reason": "评分理由（中文）"}}
    - "durability": {{"score": 0-15, "reason": "评分理由（中文）"}}
    - "source_coverage": {{"score": 0-15, "reason": "评分理由（中文）"}}
    - "factual_density": {{"score": 0-15, "reason": "评分理由（中文）"}}
    - "retrieval_value": {{"score": 0-10, "reason": "评分理由（中文）"}}
    - "novelty_vs_kb": {{"score": 0-10, "reason": "评分理由（中文）"}}
    - "structure_clarity": {{"score": 0-5, "reason": "评分理由（中文）"}}
    - "actionability": {{"score": 0-5, "reason": "评分理由（中文）"}}
    - "cross_linking": {{"score": 0-5, "reason": "评分理由（中文）"}}
    - "topic_fit": {{"score": 0-5, "reason": "评分理由（中文）"}}\
"""

_SUMMARY_WITH_SCORE_USER = """\
新文档：{doc_name}

全文：
{content}

为该文档撰写评审阶段的摘要页面（Markdown格式），并对其纳入知识库的价值进行评分。

评分意图：
- 这是知识库准入评分（不仅仅是摘要质量）。保守评分。
- 即使撰写良好，也要惩罚时效性新闻/纪要/短评类文档。
- 惩罚与已有概念高度重叠的内容。
- 奖励持久、深入、原创的投资研究。

对于投资研究报告，在原文支持时使用投资研究结构：
- 核心论点和结论
- 评级/首选观点/公司表格（如有）
- 关键数字、假设、预测和估值背景
- 产业链图谱和瓶颈
- 催化剂和监控指标
- 风险、看空证据和反驳信号
- 页面引用（如有页码标记）

确保所有重要观点可追溯至原文。保留重要数字、日期、公司和单位。仅在概念值得持久跨文档页面时使用[[concepts/...]]。

{scoring_rubric}

返回JSON对象，包含以下键：
- "brief"：一句话描述文档主要贡献（100字符以内）
- "content"：完整Markdown格式摘要
- {scorecard_shape}

仅返回有效JSON，不要代码围栏。
"""

_LOCAL_LONG_DOC_SUMMARY_WITH_SCORE_USER = """\
这是长文档"{doc_name}"的分页索引提取。

{content}

基于此分页索引提取，撰写高信号评审阶段摘要页面，并对文档纳入知识库的价值进行评分。

评分意图：
- 这是知识库准入评分（不仅仅是摘要质量）。保守评分。
- 即使撰写良好，也要惩罚时效性新闻/纪要/短评类文档。
- 惩罚与已有概念高度重叠的内容。
- 奖励持久、深入、原创的投资研究。

对于投资研究报告，保留评级、公司名称、预测、估值背景、关键数字、催化剂、风险和监控指标。
在证据可用时使用页码引用，如"第12页"。

{scoring_rubric}

返回JSON对象，包含以下键：
- "brief"：一句话描述文档主要贡献（100字符以内）
- "content"：带持久[[concepts/...]]链接的完整Markdown摘要
- {scorecard_shape}

仅返回有效JSON，不要代码围栏。
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
    from openkb.source_relations import resolve_source_document

    # Ensure ledger record has document metadata when it was created
    # as an empty shell (e.g. via batch approve before summarize).
    record = get_document_ledger_record(kb_dir, file_hash)
    needs_metadata = record is not None and not str(record.get("name") or "").strip()
    if needs_metadata:
        try:
            document = resolve_source_document(kb_dir, file_hash)
            defaults = infer_document_ledger_defaults(document)
        except (ValueError, RuntimeError):
            defaults = None
    else:
        defaults = None

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
    return upsert_document_ledger_record(kb_dir, file_hash, updates, defaults=defaults)


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
按照下方v2评分标准，对"{doc_name}"的已有评审摘要重新评分。不要重写摘要；仅返回新的评分卡对象，将摘要文本作为知识库准入候选进行评分。

已有摘要（已撰写完成）：
{summary}

{scoring_rubric}

返回JSON对象，仅包含以下顶层键：
- {scorecard_shape}

仅返回有效JSON，不要代码围栏。不要包含其他顶层键。
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
            "label": "研究深度",
            "score": min(15, 4 + min(heading_count, 5) + min(line_count, 30) // 6),
            "max": 15,
            "reason": "基于摘要长度和结构深度的启发式估计。",
        },
        "durability": {
            "label": "耐久性",
            "score": min(15, 6 + min(concept_link_count, 5)),
            "max": 15,
            "reason": "启发式估计——假设中性耐久性，无标题信号。",
        },
        "source_coverage": {
            "label": "来源覆盖",
            "score": min(15, 5 + min(line_count, 24) // 3 + min(heading_count, 3)),
            "max": 15,
            "reason": "基于摘要广度、分段和覆盖信号的启发式估计。",
        },
        "factual_density": {
            "label": "事实密度",
            "score": min(15, 3 + min(digit_count, 36) // 4),
            "max": 15,
            "reason": "基于保留数字和来源支撑事实的启发式估计。",
        },
        "retrieval_value": {
            "label": "检索价值",
            "score": min(10, 3 + min(line_count, 18) // 3 + min(len(sentences), 6) // 3),
            "max": 10,
            "reason": "基于摘要未来召回和查询价值的启发式估计。",
        },
        "novelty_vs_kb": {
            "label": "知识库新颖度",
            "score": min(10, 5),  # Neutral fallback — overlap not measurable from text alone.
            "max": 10,
            "reason": "启发式中性分数；确定性重叠由auto_review_overlap计算。",
        },
        "structure_clarity": {
            "label": "结构与清晰度",
            "score": min(5, 1 + min(heading_count, 3) + min(bullet_count, 6) // 3),
            "max": 5,
            "reason": "基于标题、列表结构和可扫描性的启发式估计。",
        },
        "actionability": {
            "label": "可操作性",
            "score": min(5, 1 + min(bullet_count, 8) // 3 + (1 if digit_count >= 6 else 0)),
            "max": 5,
            "reason": "基于摘要是否可能保留指标、风险或决策的启发式估计。",
        },
        "cross_linking": {
            "label": "交叉链接",
            "score": min(5, min(wiki_link_count, 5)),
            "max": 5,
            "reason": "基于可复用概念链接信号的启发式估计。",
        },
        "topic_fit": {
            "label": "主题契合度",
            "score": min(5, 3),  # Neutral default; topic mismatch is rare and not detectable from text alone.
            "max": 5,
            "reason": "启发式中性分数；主题契合需要知识库上下文感知。",
        },
    }
    total = sum(int(item["score"]) for item in dimensions.values())
    return {
        "version": _SUMMARY_SCORECARD_VERSION,
        "method": "heuristic_summary_value_v2",
        "overall_assessment": (
            "因结构化LLM评分卡不可用，基于摘要结构、事实密度和复用信号生成的回退评分。"
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
