"""Knowledge lint agent for semantic quality checks on the wiki."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents import Agent, Runner, function_tool

from openkb.agent.tools import list_wiki_files, read_wiki_file
from openkb.llm_runtime import build_agent_model_settings, resolve_agent_model

MAX_TURNS = 50
from openkb.schema import SCHEMA_MD, get_agents_md

_LINTER_INSTRUCTIONS_TEMPLATE = """\
You are OpenKB's semantic lint agent. Your job is to audit the wiki
for quality issues that structural tools cannot detect.

{schema_md}

## Checks to perform
1. **Contradictions** — Do any pages make conflicting claims about the same fact?
2. **Gaps** — Are there obvious missing topics or unexplained references?
3. **Staleness** — Are there references to "recent" work, dates, or versions that
   may be outdated?
4. **Redundancy** — Are there multiple pages that cover the same content and
   could be merged?
5. **Concept coverage** — Are important themes in the summaries missing concept pages?

6. **Company boundary** - Are company-specific pages placed in `companies/`
   while `concepts/` stays focused on reusable mechanisms, risks, metrics, and themes?
7. **Industry boundary** - Are industry pages in `industries/` real industries,
   sectors, or durable value-chain segments rather than products, risks, metrics,
   one-off themes, geographies, or companies?

## Process
1. Start with index.md to understand scope.
2. Read summary pages to understand document content.
3. Read company pages to check company-specific evidence, valuation context,
   catalysts, and risks.
4. Read industries/ pages to check industry boundaries and missing durable
   sector or value-chain pages.
5. Read concept pages to check for contradictions and gaps.
5. Produce a structured Markdown report listing issues found with references
   to the specific pages where each issue occurs.

Be thorough but concise. If the wiki is small or sparse, say so.
If no issues are found in a category, say "None found."
"""

_COVERAGE_GAP_SIGNAL_RE = re.compile(
    r"missing|gap|coverage|unexplained|not\s+covered|lacks?|"
    r"缺|缺失|缺少|未覆盖|未解释|没有.*(?:概念|页面)",
    re.IGNORECASE,
)


def _coverage_gap_text(report: str) -> str:
    """Extract report lines likely to describe missing semantic coverage."""
    lines: list[str] = []
    in_gap_section = False
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            in_gap_section = bool(_COVERAGE_GAP_SIGNAL_RE.search(heading))
            if in_gap_section:
                lines.append(heading)
            continue
        if in_gap_section or _COVERAGE_GAP_SIGNAL_RE.search(line):
            lines.append(line)
    return "\n".join(lines)


def extract_coverage_gap_concept_candidates(
    wiki_root: Path,
    report: str,
    *,
    max_concepts: int = 12,
) -> list[dict[str, str]]:
    """Infer missing durable concept pages from a semantic lint report."""
    gap_text = _coverage_gap_text(report)
    if not gap_text:
        return []

    from openkb.agent.compiler import _extract_concept_candidates_from_summary

    concepts_dir = wiki_root / "concepts"
    existing = {
        path.stem.casefold()
        for path in concepts_dir.glob("*.md")
    } if concepts_dir.exists() else set()

    candidates: list[dict[str, str]] = []
    for item in _extract_concept_candidates_from_summary(gap_text, max_concepts=max_concepts * 2):
        name = str(item.get("name", "")).strip()
        if not name or name.casefold() in existing:
            continue
        candidates.append(
            {
                "name": name,
                "title": str(item.get("title") or name.replace("_", " ")),
                "path": f"concepts/{name}.md",
                "action": "create",
            }
        )
        if len(candidates) >= max_concepts:
            break
    return candidates


_REPORT_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.)、])\s*(?P<body>.+?)\s*$")
_COMPANY_LIST_SPLIT_RE = re.compile(r"[、,，;；]")

_REPORT_CONCEPT_ALIASES: list[tuple[re.Pattern[str], tuple[str, str]]] = [
    (re.compile(r"云资本开支|Cloud\s*CAPEX|Cloud_CAPEX", re.IGNORECASE), ("Cloud_CAPEX", "Cloud CAPEX")),
    (re.compile(r"AI\s*CPU", re.IGNORECASE), ("AI_CPU", "AI CPU")),
    (re.compile(r"AI\s*GPU", re.IGNORECASE), ("AI_GPU", "AI GPU")),
    (re.compile(r"BMC.*服务器管理|BMC\s*芯片|Baseboard\s*Management", re.IGNORECASE), ("BMC_Server_Management", "BMC Server Management")),
    (re.compile(r"技术通胀|Technology\s*Inflation", re.IGNORECASE), ("Technology_Inflation", "Technology Inflation")),
    (re.compile(r"AI\s*推理.*训练|Inference.*Training", re.IGNORECASE), ("AI_Inference_vs_Training", "AI Inference vs Training")),
    (re.compile(r"存储周期|旧型记忆体|Memory\s*Cycle", re.IGNORECASE), ("Memory_Cycle", "Memory Cycle")),
    (re.compile(r"出口管制|Export\s*Controls?", re.IGNORECASE), ("Export_Controls", "Export Controls")),
]

_SEED_PAGE_TEMPLATES: dict[str, dict[str, str]] = {
    "industries": {
        "name": "Seed_industries",
        "title": "Semiconductor Value Chain",
        "path": "industries/semiconductor-value-chain.md",
        "brief": "Seed industry page for semiconductor value-chain structure and profit-pool tracking.",
    },
}


def _report_sections(report: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    current = "Report"
    lines: list[str] = []
    for raw_line in report.splitlines():
        line = raw_line.rstrip()
        if line.startswith("#"):
            if lines:
                sections.append((current, lines))
                lines = []
            current = line.lstrip("#").strip() or "Report"
            continue
        lines.append(line)
    if lines:
        sections.append((current, lines))
    return sections


def _strip_markdown_inline(value: str) -> str:
    value = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda match: match.group(2) or match.group(1), value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*|__", "", value)
    return value.strip()


def _normalise_report_item(raw_body: str) -> tuple[str, str]:
    body = _strip_markdown_inline(raw_body)
    title = body
    detail = ""
    if " - " in body:
        title, detail = body.split(" - ", 1)
    elif " — " in body:
        title, detail = body.split(" — ", 1)
    elif "：" in body:
        title, detail = body.split("：", 1)
    elif ": " in body:
        title, detail = body.split(": ", 1)
    return title.strip(), detail.strip()


def _existing_wiki_paths(wiki_root: Path) -> set[str]:
    if not wiki_root.exists():
        return set()
    return {
        path.relative_to(wiki_root).as_posix()
        for path in wiki_root.rglob("*.md")
        if path.is_file()
    }


def _candidate_identity(item: dict[str, str]) -> str:
    return str(item.get("path") or item.get("name") or item.get("title") or "").casefold()


def _page_type_for_path(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "wiki"


def _heading_for_page_type(page_type: str) -> str:
    return {
        "companies": "## Companies",
        "industries": "## Industries",
        "concepts": "## Concepts",
        "explorations": "## Explorations",
    }.get(page_type, "## Concepts")


def _draft_generic_page(title: str, page_type: str, reason: str) -> str:
    purpose = {
        "companies": "company-specific investment relevance",
        "industries": "industry structure, value-chain position, and profit-pool migration",
    }.get(page_type, "durable wiki knowledge")
    return (
        "---\n"
        "sources: []\n"
        f"brief: Draft page for {title}.\n"
        "status: draft\n"
        "generated_by: openkb lint fix plan\n"
        "---\n\n"
        f"# {title}\n\n"
        f"This draft was proposed from a lint report item: {reason}\n\n"
        "## Why It Matters\n"
        f"TODO: Explain the {purpose}.\n\n"
        "## Source Evidence\n"
        "TODO: Add source-summary links and page references before treating this as final knowledge.\n\n"
        "## Key Points To Track\n"
        "TODO: Add the claims, metrics, catalysts, or milestones that make this page useful.\n\n"
        "## Risks And Contra-Evidence\n"
        "TODO: Add the main ways this view could be wrong, fade, or be mispriced.\n\n"
        "## Related Pages\n"
        "TODO: Link related summaries, companies, industries, or concepts.\n"
    )


def _review_preview(title: str, reason: str, path: str) -> str:
    return (
        f"# Review: {title}\n\n"
        f"Target: `{path}`\n\n"
        f"Lint finding: {reason}\n\n"
        "This item needs a human-reviewed edit to an existing page. "
        "OpenKB will show it in the fix plan, but it will not rewrite existing content automatically."
    )


def _enrich_fix_candidate(wiki_root: Path, candidate: dict[str, str], existing_paths: set[str]) -> dict[str, str | bool]:
    path = str(candidate.get("path") or f"concepts/{candidate['name']}.md")
    page_type = str(candidate.get("type") or _page_type_for_path(path))
    action = str(candidate.get("action") or "create")
    reason = str(candidate.get("reason") or candidate.get("title") or candidate.get("name") or "")
    title = str(candidate.get("title") or candidate.get("name") or path)
    auto_applicable = action == "create" and path not in existing_paths

    if action == "create" and page_type == "concepts":
        evidence = _candidate_summary_evidence(wiki_root, str(candidate["name"]), title)
        preview = _draft_concept_page(str(candidate["name"]), title, evidence)
    elif action == "create":
        preview = _draft_generic_page(title, page_type, reason)
    else:
        preview = _review_preview(title, reason, path)

    enriched: dict[str, str | bool] = {
        "id": str(candidate.get("id") or path),
        "name": str(candidate.get("name") or Path(path).stem),
        "title": title,
        "path": path,
        "type": page_type,
        "action": action,
        "source_section": str(candidate.get("source_section") or "Lint report"),
        "reason": reason,
        "preview": preview,
        "auto_applicable": auto_applicable,
    }
    if path in existing_paths and action == "create":
        enriched["status"] = "exists"
    return enriched


def _add_candidate(
    candidates: list[dict[str, str]],
    seen: set[str],
    *,
    name: str,
    title: str,
    path: str,
    action: str,
    source_section: str,
    reason: str,
    page_type: str | None = None,
) -> None:
    item = {
        "name": name,
        "title": title,
        "path": path,
        "action": action,
        "source_section": source_section,
        "reason": reason,
    }
    if page_type:
        item["type"] = page_type
    key = _candidate_identity(item)
    if not key or key in seen:
        return
    seen.add(key)
    candidates.append(item)


def _add_concept_alias_candidates(
    candidates: list[dict[str, str]],
    seen: set[str],
    *,
    text: str,
    section: str,
) -> bool:
    matched = False
    for pattern, (name, title) in _REPORT_CONCEPT_ALIASES:
        if not pattern.search(text):
            continue
        matched = True
        _add_candidate(
            candidates,
            seen,
            name=name,
            title=title,
            path=f"concepts/{name}.md",
            action="create",
            source_section=section,
            reason=text,
            page_type="concepts",
        )
    return matched


def _add_company_candidates(
    candidates: list[dict[str, str]],
    seen: set[str],
    *,
    detail: str,
    section: str,
    reason: str,
) -> None:
    from openkb.agent.compiler import _sanitize_concept_name

    raw = re.sub(r"按.*?优先级|投資|投资", "", detail)
    for fragment in _COMPANY_LIST_SPLIT_RE.split(raw):
        name = _strip_markdown_inline(fragment).strip(" .。()（）")
        if not name or len(name) > 40:
            continue
        if re.search(r"公司页面|缺失|创建|页面|优先级|按", name):
            continue
        slug = _sanitize_concept_name(name)
        if not slug:
            continue
        _add_candidate(
            candidates,
            seen,
            name=slug,
            title=name,
            path=f"companies/{slug}.md",
            action="create",
            source_section=section,
            reason=reason,
            page_type="companies",
        )


def _table_cells(line: str) -> list[str]:
    if not line.strip().startswith("|"):
        return []
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if not cells or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
        return []
    return cells


def _add_table_concept_candidate(
    candidates: list[dict[str, str]],
    seen: set[str],
    *,
    cells: list[str],
    section: str,
) -> None:
    if len(cells) < 2:
        return
    concept = _strip_markdown_inline(cells[0])
    if not concept or re.search(r"建议创建|概念|目录|页面", concept):
        return
    reason = " | ".join(_strip_markdown_inline(cell) for cell in cells if cell)
    if _add_concept_alias_candidates(candidates, seen, text=f"{concept} {reason}", section=section):
        return

    from openkb.agent.compiler import _sanitize_concept_name

    title = concept.strip()
    slug = _sanitize_concept_name(title)
    if not slug:
        return
    _add_candidate(
        candidates,
        seen,
        name=slug,
        title=title,
        path=f"concepts/{slug}.md",
        action="create",
        source_section=section,
        reason=reason,
        page_type="concepts",
    )


def extract_lint_fix_candidates(
    wiki_root: Path,
    report: str,
    *,
    max_candidates: int = 32,
) -> list[dict[str, str | bool]]:
    """Build a concrete, reviewable fix plan from a lint report.

    The plan intentionally favors safe draft creation and explicit manual-review
    items over silent rewrites of existing wiki pages.
    """
    wiki_root = Path(wiki_root)
    existing_paths = _existing_wiki_paths(wiki_root)
    raw_candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    sections = _report_sections(report)
    priority_sections = [
        item
        for item in sections
        if re.search(r"必须修复|建议修复|must\s+fix|recommended", item[0], re.IGNORECASE)
    ]
    other_sections = [item for item in sections if item not in priority_sections]

    for section, lines in priority_sections + other_sections:
        for line in lines:
            cells = _table_cells(line)
            if cells and re.search(r"缺失|高价值|概念覆盖|Concept\s+Coverage", section, re.IGNORECASE):
                _add_table_concept_candidate(raw_candidates, seen, cells=cells, section=section)
                if len(raw_candidates) >= max_candidates:
                    break
                continue
            match = _REPORT_ITEM_RE.match(line)
            if not match:
                continue
            body = match.group("body")
            title, detail = _normalise_report_item(body)
            searchable = f"{title} {detail}".strip()
            if not searchable:
                continue
            _add_concept_alias_candidates(raw_candidates, seen, text=searchable, section=section)
            if re.search(r"创建.*公司页面|缺失.*公司", searchable):
                _add_company_candidates(
                    raw_candidates,
                    seen,
                    detail=detail or searchable,
                    section=section,
                    reason=searchable,
                )
            if re.search(r"industries|行业目录|搭建.*目录", searchable, re.IGNORECASE):
                for page_type, template in _SEED_PAGE_TEMPLATES.items():
                    _add_candidate(
                        raw_candidates,
                        seen,
                        name=template["name"],
                        title=template["title"],
                        path=template["path"],
                        action="create",
                        source_section=section,
                        reason=searchable,
                        page_type=page_type,
                    )
            if re.search(r"Optical_Engines|统一.*中文", searchable, re.IGNORECASE):
                _add_candidate(
                    raw_candidates,
                    seen,
                    name="Optical_Engines",
                    title="Optical Engines language normalization",
                    path="concepts/Optical_Engines.md",
                    action="manual-review",
                    source_section=section,
                    reason=searchable,
                    page_type="concepts",
                )
            if re.search(r"Aspeed|评级日期", searchable, re.IGNORECASE):
                _add_candidate(
                    raw_candidates,
                    seen,
                    name="Aspeed_Rating_Date",
                    title="Aspeed rating date clarification",
                    path="companies/aspeed.md",
                    action="manual-review",
                    source_section=section,
                    reason=searchable,
                    page_type="companies",
                )
            if re.search(r"死链接|broken links?|wikilink", searchable, re.IGNORECASE):
                _add_candidate(
                    raw_candidates,
                    seen,
                    name="Normalize_Concept_Links",
                    title="Normalize unresolved concept links",
                    path="wiki",
                    action="manual-review",
                    source_section=section,
                    reason=searchable,
                    page_type="wiki",
                )
            if re.search(r"去冗余|redundan", searchable, re.IGNORECASE):
                _add_candidate(
                    raw_candidates,
                    seen,
                    name="Clarify_Concept_Boundaries",
                    title="Clarify overlapping concept boundaries",
                    path="concepts/Advanced_Packaging.md",
                    action="manual-review",
                    source_section=section,
                    reason=searchable,
                    page_type="concepts",
                )
            if len(raw_candidates) >= max_candidates:
                break
        if len(raw_candidates) >= max_candidates:
            break

    for item in extract_coverage_gap_concept_candidates(wiki_root, report, max_concepts=max_candidates):
        if len(raw_candidates) >= max_candidates:
            break
        _add_candidate(
            raw_candidates,
            seen,
            name=str(item["name"]),
            title=str(item.get("title") or item["name"]),
            path=str(item.get("path") or f"concepts/{item['name']}.md"),
            action=str(item.get("action") or "create"),
            source_section="Coverage Gap Candidates",
            reason=f"Coverage-gap concept candidate: {item.get('title') or item['name']}",
            page_type="concepts",
        )

    return [
        _enrich_fix_candidate(wiki_root, candidate, existing_paths)
        for candidate in raw_candidates[:max_candidates]
    ]


def format_coverage_gap_concept_candidates(candidates: list[dict[str, str]]) -> str:
    """Format inferred semantic coverage gaps as a Markdown report section."""
    lines = ["## Coverage Gap Candidates", ""]
    if not candidates:
        lines.append("No semantic coverage-gap concept candidates found.")
        return "\n".join(lines)

    lines.append("Potential durable concept pages inferred from semantic lint gaps:")
    lines.append("")
    for item in candidates:
        name = item["name"]
        title = item.get("title") or name.replace("_", " ")
        action = item.get("action", "create")
        lines.append(f"- [[concepts/{name}]] - {title} ({action})")
    return "\n".join(lines)


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:].lstrip()


def _frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("---", 3)
    if end == -1:
        return ""
    frontmatter = text[:end]
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _summary_snippet_for_candidate(text: str, name: str, title: str, limit: int = 180) -> str:
    body = _strip_frontmatter(text)
    terms = {
        term.casefold()
        for term in re.split(r"[^A-Za-z0-9]+", f"{name} {title}")
        if len(term) >= 3
    }

    fallback = ""
    for raw_line in body.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if not fallback:
            fallback = line
        if line.startswith("#"):
            continue
        folded = line.casefold()
        if any(term in folded for term in terms):
            return line[:limit].rstrip()
    return fallback[:limit].rstrip()


def _candidate_summary_evidence(
    wiki_root: Path,
    name: str,
    title: str,
    max_items: int = 3,
) -> list[dict[str, str]]:
    from openkb.agent.compiler import _extract_concept_candidates_from_summary

    summaries_dir = wiki_root / "summaries"
    if not summaries_dir.exists():
        return []

    evidence: list[dict[str, str]] = []
    for path in sorted(summaries_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        summary_candidates = _extract_concept_candidates_from_summary(text, max_concepts=50)
        names = {str(item.get("name", "")).casefold() for item in summary_candidates}
        if name.casefold() not in names:
            continue
        page_evidence = _page_level_evidence(wiki_root, text, name, title)
        evidence.append(
            {
                "source": f"summaries/{path.name}",
                "link": f"summaries/{path.stem}",
                "snippet": (
                    page_evidence.get("snippet")
                    if page_evidence
                    else _summary_snippet_for_candidate(text, name, title)
                ),
                "page": page_evidence.get("page", "") if page_evidence else "",
            }
        )
        if len(evidence) >= max_items:
            break
    return evidence


def _page_level_evidence(
    wiki_root: Path,
    summary_text: str,
    name: str,
    title: str,
) -> dict[str, str]:
    from openkb.agent.compiler import _extract_concept_candidates_from_summary

    full_text = _frontmatter_value(summary_text, "full_text")
    if not full_text or not full_text.endswith(".json"):
        return {}

    path = (wiki_root / full_text).resolve()
    try:
        if not path.is_relative_to(wiki_root.resolve()) or not path.exists():
            return {}
        pages = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(pages, list):
        return {}

    for idx, page in enumerate(pages, start=1):
        if isinstance(page, str):
            page_num = str(idx)
            content = page
        elif isinstance(page, dict):
            page_num = str(page.get("page") or page.get("page_number") or idx)
            content = str(page.get("content") or page.get("markdown") or page.get("text") or "")
        else:
            continue
        page_candidates = _extract_concept_candidates_from_summary(content, max_concepts=50)
        page_names = {str(item.get("name", "")).casefold() for item in page_candidates}
        if name.casefold() not in page_names:
            continue
        return {
            "page": page_num,
            "snippet": _summary_snippet_for_candidate(content, name, title),
        }
    return {}


def _format_source_evidence_line(item: dict[str, str]) -> str:
    page = f" p.{item['page']}" if item.get("page") else ""
    return f"- [[{item['link']}]]{page}: {item['snippet']}"


def _draft_concept_page(name: str, title: str, evidence: list[dict[str, str]] | None = None) -> str:
    evidence = evidence or []
    brief = f"Coverage-gap draft for {title}."
    sources = ", ".join(item["source"] for item in evidence) if evidence else ""
    source_lines = "\n".join(
        _format_source_evidence_line(item)
        for item in evidence
    )
    if not source_lines:
        source_lines = "TODO: Add links to source summaries and exact page references where available."

    return (
        "---\n"
        f"sources: [{sources}]\n"
        f"brief: {brief}\n"
        "status: draft\n"
        "generated_by: openkb lint --fix\n"
        "---\n\n"
        f"# {title}\n\n"
        "This draft was created from semantic lint coverage-gap candidates. "
        "Review it and replace the TODOs with source-backed investment knowledge.\n\n"
        "## Why It Matters\n"
        "TODO: Explain why this concept matters for long-term investment research.\n\n"
        "## Source Evidence\n"
        f"{source_lines}\n\n"
        "## Key Metrics To Track\n"
        "TODO: Add the operating, financial, valuation, or policy indicators that should be monitored.\n\n"
        "## Risks And Contra-Evidence\n"
        "TODO: Add the main ways this concept could be wrong, fade, or be mispriced.\n\n"
        "## Related Concepts\n"
        "TODO: Link related durable concept pages.\n"
    )


def _ensure_index_concept_entry(wiki_root: Path, name: str, title: str) -> None:
    index_path = wiki_root / "index.md"
    if index_path.exists():
        lines = index_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = [
            "# Knowledge Base Index",
            "",
            "## Documents",
            "",
            "## Companies",
            "",
            "## Industries",
            "",
            "## Concepts",
            "",
            "## Explorations",
        ]

    if "## Concepts" not in lines:
        insert_at = lines.index("## Explorations") if "## Explorations" in lines else len(lines)
        block = ["## Concepts", ""]
        if insert_at > 0 and lines[insert_at - 1] != "":
            block.insert(0, "")
        lines[insert_at:insert_at] = block

    link = f"[[concepts/{name}]]"
    if any(line.startswith(f"- {link}") for line in lines):
        return

    start = lines.index("## Concepts") + 1
    lines.insert(start, f"- {link} - {title} (coverage-gap draft)")
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _ensure_index_page_entry(wiki_root: Path, page_path: str, title: str, label: str = "lint draft") -> None:
    if page_path.startswith("concepts/"):
        _ensure_index_concept_entry(wiki_root, Path(page_path).stem, title)
        return

    index_path = wiki_root / "index.md"
    if index_path.exists():
        lines = index_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = ["# Knowledge Base Index", ""]

    heading = _heading_for_page_type(_page_type_for_path(page_path))
    if heading not in lines:
        insert_at = lines.index("## Concepts") if "## Concepts" in lines else len(lines)
        block = [heading, ""]
        if insert_at > 0 and lines[insert_at - 1] != "":
            block.insert(0, "")
        lines[insert_at:insert_at] = block

    link = f"[[{Path(page_path).with_suffix('').as_posix()}]]"
    if any(line.startswith(f"- {link}") for line in lines):
        return

    start = lines.index(heading) + 1
    lines.insert(start, f"- {link} - {title} ({label})")
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _update_evidence_map(wiki_root: Path, page_path: str, evidence: list[dict[str, str]]) -> None:
    from openkb.agent.evidence import update_evidence_map

    update_evidence_map(wiki_root, page_path, evidence)


def apply_coverage_gap_concept_candidates(
    wiki_root: Path,
    candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Create reviewable draft concept pages for coverage-gap candidates.

    Existing pages are never overwritten. The returned list contains only
    candidates whose draft page was actually created.
    """
    from openkb.agent.compiler import _sanitize_concept_name

    concepts_dir = wiki_root / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    created: list[dict[str, str]] = []
    concepts_root = concepts_dir.resolve()
    for item in candidates:
        raw_name = str(item.get("name", "")).strip()
        if not raw_name:
            continue
        name = _sanitize_concept_name(raw_name)
        title = str(item.get("title") or name.replace("_", " ")).strip()
        path = (concepts_dir / f"{name}.md").resolve()
        if not path.is_relative_to(concepts_root):
            continue
        if path.exists():
            continue

        evidence = _candidate_summary_evidence(wiki_root, name, title)
        path.write_text(_draft_concept_page(name, title, evidence), encoding="utf-8")
        page_path = f"concepts/{name}.md"
        created_item = {
            "name": name,
            "title": title,
            "path": page_path,
            "action": "created",
        }
        created.append(created_item)
        _ensure_index_concept_entry(wiki_root, name, title)
        _update_evidence_map(wiki_root, page_path, evidence)

    return created


_AUTO_FIX_DIRS = {"concepts", "companies", "industries", "explorations"}


def _resolve_fix_create_path(wiki_root: Path, item: dict[str, str]) -> tuple[Path, str] | None:
    from openkb.agent.compiler import _sanitize_concept_name

    raw_path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
    if not raw_path:
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            return None
        raw_path = f"concepts/{_sanitize_concept_name(raw_name)}.md"
    if not raw_path.endswith(".md"):
        raw_path = f"{raw_path}.md"

    parts = Path(raw_path).parts
    if not parts or parts[0] not in _AUTO_FIX_DIRS:
        return None

    root = wiki_root.resolve()
    full_path = (root / raw_path).resolve()
    if not full_path.is_relative_to(root):
        return None
    return full_path, Path(raw_path).as_posix()


def apply_lint_fix_candidates(
    wiki_root: Path,
    candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Apply approved lint fix candidates that are safe draft-page creates."""
    wiki_root = Path(wiki_root)
    created: list[dict[str, str]] = []
    for item in candidates:
        if str(item.get("action") or "create").strip().lower() != "create":
            continue
        resolved = _resolve_fix_create_path(wiki_root, item)
        if resolved is None:
            continue
        path, relative_path = resolved
        if path.exists():
            continue

        name = str(item.get("name") or Path(relative_path).stem).strip()
        title = str(item.get("title") or name.replace("_", " ")).strip()
        page_type = _page_type_for_path(relative_path)
        if page_type == "concepts":
            evidence = _candidate_summary_evidence(wiki_root, name, title)
            content = _draft_concept_page(name, title, evidence)
        else:
            evidence = []
            content = _draft_generic_page(title, page_type, str(item.get("reason") or title))

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created_item = {
            "name": name,
            "title": title,
            "path": relative_path,
            "action": "created",
        }
        created.append(created_item)
        _ensure_index_page_entry(wiki_root, relative_path, title, "lint draft")
        if evidence:
            _update_evidence_map(wiki_root, relative_path, evidence)

    return created


def format_coverage_gap_fixes(created: list[dict[str, str]]) -> str:
    """Format pages created by ``openkb lint --fix``."""
    lines = ["## Coverage Gap Fixes", ""]
    if not created:
        lines.append("No coverage-gap draft concept pages were created.")
        return "\n".join(lines)

    lines.append("Created reviewable draft concept pages:")
    lines.append("")
    for item in created:
        name = item["name"]
        title = item.get("title") or name.replace("_", " ")
        path = item.get("path", f"concepts/{name}.md")
        lines.append(f"- [[concepts/{name}]] - {title} ({path})")
    return "\n".join(lines)


def build_lint_agent(wiki_root: str, model: str, language: str = "en") -> Agent:
    """Build the semantic knowledge-lint agent.

    Args:
        wiki_root: Absolute path to the wiki directory.
        model: LLM model name.
        language: Language code for wiki content (e.g. 'en', 'fr').

    Returns:
        Configured :class:`~agents.Agent` instance.
    """
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _LINTER_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write the lint report in {language} language."

    @function_tool
    def list_files(directory: str) -> str:
        """List all Markdown files in a wiki subdirectory.

        Args:
            directory: Subdirectory path relative to wiki root (e.g. 'summaries').
        """
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.

        Args:
            path: File path relative to wiki root (e.g. 'summaries/paper.md').
        """
        return read_wiki_file(path, wiki_root)

    return Agent(
        name="wiki-linter",
        instructions=instructions,
        tools=[list_files, read_file],
        model=resolve_agent_model(model),
        model_settings=build_agent_model_settings(parallel_tool_calls=False, model=model),
    )


async def run_knowledge_lint(kb_dir: Path, model: str) -> str:
    """Run the semantic knowledge lint agent against the wiki.

    Args:
        kb_dir: Root of the knowledge base.
        model: LLM model name.

    Returns:
        The agent's lint report as a Markdown string.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    agent = build_lint_agent(wiki_root, model, language=language)

    prompt = (
        "Please audit this knowledge base wiki for semantic quality issues: "
        "contradictions, gaps, staleness, redundancy, and missing concept pages. "
        "Start with index.md, then read summaries, companies, investment schema pages, "
        "and concepts as needed. "
        "Produce a structured Markdown report."
    )

    result = await Runner.run(agent, prompt, max_turns=MAX_TURNS)
    return result.final_output or "Knowledge lint completed. No output produced."
