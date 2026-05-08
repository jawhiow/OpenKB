from __future__ import annotations

import argparse
from pathlib import Path

from _runtime import (
    brief_from_text,
    emit_json,
    load_evidence_map,
    markdown_pages,
    read_text,
    resolve_kb,
    score_text,
    snippet,
    split_frontmatter,
    title_from_text,
    tokenize,
    tool_available,
    wikilinks,
    wiki_root,
)


def category_weight(category: str, query: str) -> int:
    lower = query.lower()
    if category == "companies" and any(term in lower for term in ["company", "ticker", "valuation", "rating", "公司", "估值", "评级"]):
        return 18
    if category == "industries" and any(term in lower for term in ["industry", "sector", "value chain", "行业", "产业链", "竞争格局"]):
        return 16
    if category == "concepts" and any(term in lower for term in ["concept", "theme", "risk", "mechanism", "概念", "主题", "风险", "机制"]):
        return 14
    if category == "summaries" and any(term in lower for term in ["source", "report", "paper", "document", "报告", "原文", "文档"]):
        return 12
    return 0


def search(kb: str, query: str, top_k: int = 10) -> dict:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings, "results": []}

    wiki = wiki_root(kb_root)
    query_terms = tokenize(query)
    phrase = " ".join(query.lower().split())
    evidence = load_evidence_map(wiki)
    results: list[dict] = []

    for path in markdown_pages(wiki, content_only=True):
        rel = path.relative_to(wiki).as_posix()
        text = read_text(path)
        metadata, body = split_frontmatter(text)
        title = title_from_text(text, path.stem)
        brief = brief_from_text(text)
        category = rel.split("/", 1)[0] if "/" in rel else "index"
        score = 0
        score += score_text(rel.replace("/", " "), query_terms, phrase) * 5
        score += score_text(title, query_terms, phrase) * 5
        score += score_text(brief, query_terms, phrase) * 4
        score += score_text(body, query_terms, phrase)
        score += category_weight(category, query)
        if score <= 0 and query_terms:
            continue
        results.append(
            {
                "path": rel,
                "page_id": rel[:-3] if rel.endswith(".md") else rel,
                "category": category,
                "title": title,
                "brief": brief,
                "score": score,
                "snippet": snippet(body or text, query_terms),
                "wikilinks": wikilinks(text)[:20],
                "frontmatter": {
                    key: metadata.get(key, "")
                    for key in ("doc_type", "full_text", "brief", "status")
                    if key in metadata
                },
                "has_evidence": rel in evidence or (rel[:-3] if rel.endswith(".md") else rel) in evidence,
            }
        )

    results.sort(key=lambda item: (-item["score"], item["path"]))
    limit = min(max(int(top_k), 1), 50)
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "wiki_root": str(wiki),
        "query": query,
        "engine": "qmd-detected-fallback" if tool_available("qmd") else "fallback-keyword-bm25-lite",
        "results": results[:limit],
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Search an OpenKB wiki locally.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--query", required=True, help="Search query.")
    parser.add_argument("--top-k", type=int, default=10, help="Maximum results.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = search(args.kb, args.query, args.top_k)
    if args.json:
        emit_json(data)
        return
    if not data["ok"]:
        print(data["error"])
        return
    for item in data["results"]:
        print(f"{item['score']:>4} {item['path']} - {item['title']}")
        if item["snippet"]:
            print(f"     {item['snippet']}")


if __name__ == "__main__":
    main()
