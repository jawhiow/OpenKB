from __future__ import annotations

import argparse
import json
from pathlib import Path

from _runtime import (
    config_language,
    emit_json,
    load_evidence_map,
    package_available,
    read_text,
    resolve_kb,
    split_frontmatter,
    wiki_root,
)
from search_wiki import search


def classify_question(question: str, result_paths: list[str]) -> str:
    lower = question.lower()
    if any(term in lower for term in ["figure", "chart", "table", "image", "diagram", "图", "表格", "图片"]):
        return "figure_or_table"
    if any(term in lower for term in ["compare", "overview", "summarize", "all", "across", "对比", "总结", "全局", "有哪些", "主要"]):
        return "global_synthesis"
    if any(term in lower for term in ["why", "how", "deep", "drift", "drill", "深入", "拆解", "为什么", "如何"]):
        return "deep_dive"
    if result_paths and result_paths[0].startswith("companies/"):
        return "entity_company"
    if result_paths and result_paths[0].startswith("concepts/"):
        return "concept_theme"
    if any(term in lower for term in ["company", "valuation", "rating", "ticker", "公司", "估值", "评级"]):
        return "entity_company"
    if any(term in lower for term in ["concept", "risk", "theme", "mechanism", "概念", "风险", "主题", "机制"]):
        return "concept_theme"
    return "fact_lookup"


def long_document_hints(wiki: Path, question: str, results: list[dict]) -> list[dict]:
    hints: list[dict] = []
    for item in results:
        rel = item.get("path", "")
        if not rel.startswith("summaries/"):
            continue
        path = wiki / rel
        metadata, _ = split_frontmatter(read_text(path))
        doc_type = metadata.get("doc_type", "").lower()
        full_text = metadata.get("full_text", "")
        if doc_type not in {"pageindex", "local-long"} and not full_text.endswith(".json"):
            continue
        doc_name = Path(full_text).stem if full_text else Path(rel).stem
        hint: dict = {
            "summary": rel,
            "doc_name": doc_name,
            "doc_type": doc_type or "json-source",
            "full_text": full_text,
            "recommended_next_step": f"Search pages for {doc_name}, then read tight page ranges only.",
        }
        if package_available("openkb"):
            try:
                from openkb.agent.tools import search_long_document_pages

                hint["openkb_page_search_preview"] = search_long_document_pages(
                    question,
                    str(wiki),
                    doc_name=doc_name,
                    top_k=5,
                )
            except Exception as exc:
                hint["openkb_page_search_error"] = str(exc)
        hints.append(hint)
    return hints


def build_context(kb: str, question: str, top_k: int = 10) -> dict:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    wiki = wiki_root(kb_root)
    search_data = search(str(kb_root), question, top_k=top_k)
    results = search_data.get("results", [])
    result_paths = [item["path"] for item in results]
    evidence_map = load_evidence_map(wiki)
    evidence_matches: dict[str, list] = {}
    for path in result_paths:
        key_candidates = [path, path[:-3] if path.endswith(".md") else path]
        for key in key_candidates:
            if key in evidence_map:
                evidence_matches[path] = evidence_map[key]
                break

    classification = classify_question(question, result_paths)
    read_set = ["index.md"]
    for path in result_paths[:8]:
        if path not in read_set:
            read_set.append(path)
    if evidence_matches and "evidence_map.json" not in read_set:
        read_set.append("evidence_map.json")

    return {
        "ok": True,
        "kb_root": str(kb_root),
        "wiki_root": str(wiki),
        "language": config_language(kb_root),
        "question": question,
        "query_type": classification,
        "strategy": strategy_for_type(classification),
        "candidate_pages": results,
        "read_set_suggestion": read_set,
        "evidence_matches": evidence_matches,
        "long_document_hints": long_document_hints(wiki, question, results),
        "answer_contract": {
            "cite_every_claim": True,
            "citation_examples": ["[[concepts/example]]", "[[summaries/report]] p.7", "sources/report.json pages 7-8"],
            "include_read_set": True,
            "save_policy": "Do not write explorations unless the user explicitly asks to save or persist the answer.",
        },
        "warnings": warnings,
    }


def strategy_for_type(query_type: str) -> list[str]:
    strategies = {
        "fact_lookup": ["Read index.md.", "Read the top candidate pages.", "Use evidence_map.json or source pages only when the claim needs exact support."],
        "entity_company": ["Read matching companies/ pages first.", "Cross-check summaries and concepts for catalysts, risks, valuation context, and exposure chains."],
        "concept_theme": ["Read matching concepts/ pages first.", "Pull summaries and evidence for concrete examples and contra-evidence."],
        "global_synthesis": ["Group candidate pages by category.", "Read representative pages per group.", "Synthesize across groups and cite each major claim."],
        "deep_dive": ["Break the question into 3-5 subquestions.", "Search each subquestion.", "Merge answers and list remaining uncertainty."],
        "figure_or_table": ["Find source pages or image paths.", "Inspect images when available.", "Do not infer visual details from captions alone."],
    }
    return strategies.get(query_type, strategies["fact_lookup"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an OpenKB query context pack.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--question", required=True, help="Question to answer from the wiki.")
    parser.add_argument("--top-k", type=int, default=10, help="Maximum candidate pages.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = build_context(args.kb, args.question, args.top_k)
    if args.json:
        emit_json(data)
        return
    if not data["ok"]:
        print(data["error"])
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
