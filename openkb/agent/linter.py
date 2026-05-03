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

## Process
1. Start with index.md to understand scope.
2. Read summary pages to understand document content.
3. Read company pages to check company-specific evidence, valuation context,
   catalysts, and risks.
4. Read industries/, themes/, metrics/, and risks/ pages to check investment
   schema boundaries and missing durable pages.
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
            "## Themes",
            "",
            "## Metrics",
            "",
            "## Risks",
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
