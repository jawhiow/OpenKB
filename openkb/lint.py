"""Structural lint checks for the OpenKB wiki.

Checks for:
- Broken [[wikilinks]] — link targets that don't exist
- Orphaned pages — pages with no incoming or outgoing links
- Missing wiki entries — raw files without corresponding sources/summaries
- Index sync — index.md links vs actual files on disk
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from openkb.schema import LEGACY_WIKI_DIRS

# Matches [[wikilink]] or [[subdir/link]]
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_SOURCE_LIST_RE = re.compile(r"^sources:\s*\[([^\]]*)\]\s*$", re.MULTILINE)
_DOC_TYPE_RE = re.compile(r"^doc_type:\s*([^\s]+)\s*$", re.MULTILINE)
_TICKER_RE = re.compile(r"\b\d{4,6}\.(?:TW|TWO|HK|SS|SZ|KS|KQ)\b", re.IGNORECASE)
_RATING_RE = re.compile(
    r"(目标价|评级|超配|低配|等权|持股观望|Overweight|Equal-weight|Underweight|\bOW\b|\bEW\b|\bUW\b)",
    re.IGNORECASE,
)
_COMPANY_DESCRIPTOR_RE = re.compile(
    r"(公司|厂商|供应商|制造商|代工厂|设计服务|龙头|semiconductor company|foundry|supplier)",
    re.IGNORECASE,
)

_ACTIVE_GENERATED_DIRS = ("companies", "industries", "concepts")
_PAGE_REF_RE = re.compile(r"\bp\.\s*\d+\b", re.IGNORECASE)
_WIKI_NAMESPACE_PATH_RE = re.compile(
    r"(?<![\[\w:/.-])(?P<namespace>concepts|themes|metrics|risks|companies|industries)/"
    r"(?P<slug>[^\s\]\)>.,;:，。；：、（）()「」『』【】*]+)"
)
_MISNAMESPACED_CONCEPT_PREFIXES = (
    "themes-",
    "risks-",
    "metrics-",
    "companies-",
    "industries-",
)
_PLACEHOLDER_RE = re.compile(
    "|".join([
        r"\bTODO\b",
        r"add exact supporting claims",
        r"numbers need to be extracted",
        r"key financial numbers need",
        r"\u9700\u67e5\u9605\u62a5\u8868",
        r"\u9700\u67e5\u95b1\u5831\u8868",
        r"\u9700\u4ece\u62a5\u8868\u4e2d\u63d0\u53d6",
        r"\u9700\u5f9e\u5831\u8868\u4e2d\u63d0\u53d6",
        r"\u5173\u952e\u6570\u5b57\u9700",
        r"\u95dc\u9375\u6578\u5b57\u9700",
        r"\u5177\u4f53\u6570\u503c\u9700",
        r"\u5177\u9ad4\u6578\u503c\u9700",
    ]),
    re.IGNORECASE,
)
_LEGACY_GENERATED_PLACEHOLDER_LINE_PATTERNS = (
    re.compile(
        r"^\s*-\s*\[\[summaries/[^\]]+\]\](?:\s*p\.\s*\d+)?\s*:\s*"
        r"TODO:\s*add exact supporting claims and page references\.?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*TODO:\s*add exact supporting claims and page references\.?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*TODO:\s*Add source-summary links and "
        r"(?:exact )?page references(?:\s+where available|"
        r"\s+for the durable claims above)?\.?\s*$",
        re.IGNORECASE,
    ),
)

# Files to exclude from lint scanning (schema, logs, etc.)
_EXCLUDED_FILES = {"AGENTS.md", "SCHEMA.md", "log.md"}
_IGNORED_LINT_TOP_LEVEL_DIRS = {"reports", "sources", *LEGACY_WIKI_DIRS}


def _read_md(path: Path) -> str:
    """Read a Markdown file safely, returning empty string on error."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _should_skip_lint_page(wiki: Path, path: Path) -> bool:
    rel_parts = path.relative_to(wiki).parts
    return bool(rel_parts and rel_parts[0] in _IGNORED_LINT_TOP_LEVEL_DIRS)


def _all_wiki_pages(wiki: Path) -> dict[str, Path]:
    """Return a mapping of stem/relative-path → absolute Path for all .md files.

    Keys are normalized: 'concepts/attention', 'summaries/paper', 'index', etc.
    """
    pages: dict[str, Path] = {}
    for md in wiki.rglob("*.md"):
        if md.name in _EXCLUDED_FILES or _should_skip_lint_page(wiki, md):
            continue
        rel = md.relative_to(wiki)
        # Store both the full relative path without extension and the stem
        key = str(rel.with_suffix("")).replace("\\", "/")
        pages[key] = md
        # Also index by stem alone for convenience
        pages[md.stem] = md
    return pages


def _extract_wikilinks(text: str) -> list[str]:
    """Return all wikilink targets found in *text*.

    Handles ``[[target|display text]]`` alias syntax — only the target is returned.
    """
    raw = _WIKILINK_RE.findall(text)
    return [link.split("|")[0].strip() for link in raw]


def _frontmatter_sources(text: str) -> list[str]:
    """Extract source paths from simple ``sources: [a, b]`` frontmatter."""
    match = _SOURCE_LIST_RE.search(text)
    if not match:
        return []
    return [
        item.strip()
        for item in match.group(1).split(",")
        if item.strip()
    ]


def _frontmatter_doc_type(text: str) -> str:
    """Extract the summary doc_type from simple frontmatter."""
    match = _DOC_TYPE_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def _summary_doc_types(wiki: Path) -> dict[str, str]:
    summaries_dir = wiki / "summaries"
    if not summaries_dir.exists():
        return {}
    return {
        f"summaries/{path.name}": _frontmatter_doc_type(_read_md(path))
        for path in summaries_dir.glob("*.md")
    }


def _strip_frontmatter(text: str) -> str:
    """Remove leading YAML-style frontmatter when present."""
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:].lstrip()


def _looks_like_company_concept(stem: str, text: str) -> bool:
    """Heuristic for company pages that were accidentally put in concepts/."""
    body = _strip_frontmatter(text)
    intro = body.split("\n## ", 1)[0][:900]
    has_rating_or_ticker = bool(_RATING_RE.search(intro) or _TICKER_RE.search(intro))
    has_company_descriptor = bool(_COMPANY_DESCRIPTOR_RE.search(intro))
    title_repeats_stem = stem in intro[:300]
    return has_rating_or_ticker and has_company_descriptor and title_repeats_stem


def _is_misnamespaced_concept_target(target: str) -> bool:
    target_norm = target.strip().strip("/")
    if not target_norm.startswith("concepts/"):
        return False
    slug = target_norm[len("concepts/"):].casefold()
    return any(slug.startswith(prefix) for prefix in _MISNAMESPACED_CONCEPT_PREFIXES)


def find_broken_links(wiki: Path) -> list[str]:
    """Scan all wiki pages for [[wikilinks]] pointing to non-existent targets.

    Args:
        wiki: Path to the wiki root directory.

    Returns:
        List of error strings describing each broken link.
    """
    pages = _all_wiki_pages(wiki)
    errors: list[str] = []

    for md in wiki.rglob("*.md"):
        if md.name in _EXCLUDED_FILES or _should_skip_lint_page(wiki, md):
            continue
        text = _read_md(md)
        for target in _extract_wikilinks(text):
            # Normalise target: strip leading/trailing whitespace and slashes
            target_norm = target.strip().strip("/")
            # Check if target resolves as a key in our page map
            if target_norm not in pages:
                rel = md.relative_to(wiki)
                errors.append(f"Broken link [[{target}]] in {rel}")

    return sorted(errors)


def find_orphans(wiki: Path) -> list[str]:
    """Find pages that have no links to or from other pages.

    A page is orphaned if:
    - No other page links to it (no incoming links), AND
    - It has no outgoing wikilinks itself.

    index.md is excluded from orphan detection.

    Args:
        wiki: Path to the wiki root directory.

    Returns:
        List of relative page paths that are orphaned.
    """
    # Exclude index, schema, log, and generated support folders.
    all_mds = [
        p for p in wiki.rglob("*.md")
        if p.name not in {"index.md", *_EXCLUDED_FILES}
        and not _should_skip_lint_page(wiki, p)
    ]
    if not all_mds:
        return []

    # Build outgoing links per page
    outgoing: dict[str, set[str]] = {}
    for md in all_mds:
        rel = str(md.relative_to(wiki).with_suffix("")).replace("\\", "/")
        text = _read_md(md)
        outgoing[rel] = set(_extract_wikilinks(text))

    # Build incoming link set (which pages are linked to)
    incoming: set[str] = set()
    for links in outgoing.values():
        for lnk in links:
            incoming.add(lnk.strip().strip("/"))
        # Also add stems
        for lnk in links:
            incoming.add(Path(lnk.strip()).stem)

    orphans: list[str] = []
    for rel, links in outgoing.items():
        stem = Path(rel).stem
        has_incoming = rel in incoming or stem in incoming
        has_outgoing = bool(links)
        if not has_incoming and not has_outgoing:
            orphans.append(rel)

    return sorted(orphans)


def find_missing_entries(raw: Path, wiki: Path) -> list[str]:
    """Find files in raw/ that have no corresponding wiki entries.

    A file is considered "present" if it has either a sources/ or summaries/
    page with the same stem.

    Args:
        raw: Path to the raw documents directory.
        wiki: Path to the wiki root directory.

    Returns:
        List of filenames in raw/ with no wiki entry.
    """
    sources_dir = wiki / "sources"
    summaries_dir = wiki / "summaries"

    sources_stems = {p.stem for p in sources_dir.glob("*.md")} if sources_dir.exists() else set()
    summary_stems = {p.stem for p in summaries_dir.glob("*.md")} if summaries_dir.exists() else set()
    known_stems = sources_stems | summary_stems

    missing: list[str] = []
    if raw.exists():
        for f in raw.iterdir():
            if f.is_file() and f.stem not in known_stems:
                missing.append(f.name)

    return sorted(missing)


def _source_stems(wiki: Path) -> set[str]:
    sources_dir = wiki / "sources"
    if not sources_dir.exists():
        return set()
    return {
        path.stem
        for path in sources_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
    }


def find_incomplete_entries(raw: Path, wiki: Path) -> list[str]:
    """Find raw files converted into sources/ but missing compiled summaries."""
    summaries_dir = wiki / "summaries"
    sources_stems = _source_stems(wiki)
    summary_stems = {p.stem for p in summaries_dir.glob("*.md")} if summaries_dir.exists() else set()

    incomplete: list[str] = []
    if raw.exists():
        for f in raw.iterdir():
            if f.is_file() and f.stem in sources_stems and f.stem not in summary_stems:
                incomplete.append(f.name)
    return sorted(incomplete)


def check_index_sync(wiki: Path) -> list[str]:
    """Compare index.md wikilinks against actual files on disk.

    Returns issues for:
    - Links in index.md pointing to non-existent pages
    - Pages in summaries/, companies/, industries/, or concepts/ not mentioned
      in index.md

    Args:
        wiki: Path to the wiki root directory.

    Returns:
        List of sync issue strings.
    """
    index_path = wiki / "index.md"
    issues: list[str] = []

    if not index_path.exists():
        return ["index.md does not exist"]

    index_text = _read_md(index_path)
    index_links = set(_extract_wikilinks(index_text))
    pages = _all_wiki_pages(wiki)

    # Check that all index links resolve
    for lnk in index_links:
        lnk_norm = lnk.strip().strip("/")
        if lnk_norm not in pages:
            issues.append(f"index.md links to missing page: [[{lnk}]]")

    # Check that generated/investment wiki pages are mentioned in index
    index_stems = {Path(lnk.strip()).stem for lnk in index_links}
    index_text_lower = index_text.lower()

    for subdir in ("summaries", "companies", "industries", "concepts"):
        subdir_path = wiki / subdir
        if not subdir_path.exists():
            continue
        for md in sorted(subdir_path.glob("*.md")):
            stem = md.stem
            if stem not in index_stems and stem.lower() not in index_text_lower:
                issues.append(f"{subdir}/{stem}.md not mentioned in index.md")

    return sorted(issues)


def find_investment_quality_issues(
    wiki: Path,
    *,
    max_concepts_per_summary: int = 12,
) -> list[str]:
    """Find investment-specific wiki quality risks.

    These checks are intentionally report-only. They flag patterns that usually
    indicate noisy investment KB generation, especially company pages being
    routed into ``concepts/`` and a single report spawning too many concepts.
    """
    issues: list[str] = []
    concepts_by_summary: dict[str, list[str]] = {}
    summary_doc_types = _summary_doc_types(wiki)

    concepts_dir = wiki / "concepts"
    if concepts_dir.exists():
        for md in sorted(concepts_dir.glob("*.md")):
            text = _read_md(md)
            rel = str(md.relative_to(wiki)).replace("\\", "/")
            if _looks_like_company_concept(md.stem, text):
                issues.append(
                    f"company-like concept page: {rel} appears to describe a company; "
                    "route this content to companies/ when the investment schema is enabled."
                )
            for source in _frontmatter_sources(text):
                if source.startswith("summaries/"):
                    concepts_by_summary.setdefault(source, []).append(rel)

    for subdir in _ACTIVE_GENERATED_DIRS:
        pages_dir = wiki / subdir
        if not pages_dir.exists():
            continue
        for md in sorted(pages_dir.glob("*.md")):
            text = _read_md(md)
            rel = str(md.relative_to(wiki)).replace("\\", "/")
            body = _strip_frontmatter(text)

            if _PLACEHOLDER_RE.search(body):
                issues.append(
                    f"placeholder generated content: {rel} contains TODO or extraction placeholder text."
                )

            for target in _extract_wikilinks(body):
                if _is_misnamespaced_concept_target(target):
                    issues.append(
                        f"misnamespaced wikilink: {rel} links [[{target}]]; "
                        "use a valid active wiki page or plain text."
                    )

            body_without_links = _WIKILINK_RE.sub("", body)
            for match in _WIKI_NAMESPACE_PATH_RE.finditer(body_without_links):
                namespace = match.group("namespace")
                slug = match.group("slug").rstrip(".,;:")
                issues.append(
                    f"bare wiki namespace reference: {rel} contains {namespace}/{slug}; "
                    "use a valid wikilink or plain text."
                )

            sources = _frontmatter_sources(text)
            if (
                subdir == "companies"
                and any(summary_doc_types.get(source) == "pageindex" for source in sources)
                and not _PAGE_REF_RE.search(body)
            ):
                issues.append(
                    f"missing page evidence: {rel} is sourced from a PageIndex summary "
                    "but has no p.N page references."
                )

    for source, pages in sorted(concepts_by_summary.items()):
        if len(pages) > max_concepts_per_summary:
            issues.append(
                f"concept explosion: {source} is linked as a source for {len(pages)} "
                f"concept pages; expected <= {max_concepts_per_summary}. "
                "This often means company names were generated as concepts."
            )

    return sorted(issues)


def _clean_legacy_generated_placeholder_lines(text: str) -> tuple[str, int]:
    """Remove old machine-written placeholder lines, preserving user TODOs."""
    removed = 0
    lines: list[str] = []
    for line in text.splitlines():
        if any(pattern.match(line) for pattern in _LEGACY_GENERATED_PLACEHOLDER_LINE_PATTERNS):
            removed += 1
            continue
        lines.append(line)

    if not removed:
        return text, 0

    cleaned = "\n".join(lines)
    if text.endswith("\n") and cleaned:
        cleaned += "\n"
    return cleaned, removed


def cleanup_legacy_generated_placeholders(wiki: Path) -> list[dict[str, str | int]]:
    """Delete legacy placeholder lines from active generated wiki pages only."""
    cleaned_pages: list[dict[str, str | int]] = []
    for subdir in _ACTIVE_GENERATED_DIRS:
        pages_dir = wiki / subdir
        if not pages_dir.exists():
            continue
        for md in sorted(pages_dir.glob("*.md")):
            text = _read_md(md)
            cleaned, removed = _clean_legacy_generated_placeholder_lines(text)
            if not removed:
                continue
            md.write_text(cleaned, encoding="utf-8")
            cleaned_pages.append({
                "path": str(md.relative_to(wiki)).replace("\\", "/"),
                "removed_lines": removed,
            })
    return cleaned_pages


def format_legacy_placeholder_fixes(cleaned: list[dict[str, str | int]]) -> str:
    """Format pages cleaned by ``openkb lint --fix``."""
    lines = ["## Legacy Placeholder Fixes", ""]
    if not cleaned:
        lines.append("No legacy generated placeholder lines found.")
        return "\n".join(lines)

    lines.append(f"Cleaned {len(cleaned)} file(s).")
    for item in cleaned:
        lines.append(f"- {item['path']} - removed {item['removed_lines']} line(s)")
    return "\n".join(lines)


def find_concept_duplicate_clusters(kb_dir: Path, max_clusters: int = 20) -> list[str]:
    """Surface duplicate-concept clusters detected by ``concept_merge``.

    Returns formatted bullet strings suitable for inclusion in the lint
    report. Only the top ``max_clusters`` are returned to keep reports
    readable; the full set is still available via ``openkb merge-concepts``.
    """
    try:
        from openkb.concept_merge import propose_merges
    except ImportError:
        return []
    proposals = propose_merges(kb_dir)
    issues: list[str] = []
    for proposal in proposals[:max_clusters]:
        siblings = proposal.merged[1:]
        if not siblings:
            continue
        joined = ", ".join(siblings[:4]) + (" ..." if len(siblings) > 4 else "")
        issues.append(
            f"duplicate concept cluster (canonical={proposal.canonical}, "
            f"size={len(proposal.merged)}): merge candidates → {joined}"
        )
    if len(proposals) > max_clusters:
        issues.append(
            f"... and {len(proposals) - max_clusters} more clusters; run "
            "`openkb merge-concepts` to see the full list."
        )
    return issues


_H1_NOISE_PREFIX_RE = re.compile(
    r"^(概念[:：]|主题[:：]|Concept\s*[:：]|Topic\s*[:：])\s*",
    re.IGNORECASE,
)


def _h1_bigram_set(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = re.sub(r"[\s\-_/（）()【】\[\]，,。.：:、；;]+", "", text)
    if len(text) <= 1:
        return {text} if text else set()
    return {text[i:i+2] for i in range(len(text) - 1)}


def _h1_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_h1_issues(wiki: Path, *, namespaces: tuple[str, ...] = ("concepts", "companies", "industries")) -> list[str]:
    """Audit H1 / filename consistency across the named subdirectories.

    Surfaces four failure modes (matching ``scripts/audit_concept_h1.py``):
      * ``missing_h1``: page has no top-level heading at all.
      * ``h1_is_english_slug``: H1 text is just the English slug (no human title).
      * ``h1_prefix_noise``: H1 begins with ``概念：`` / ``Concept:`` etc.
      * ``h1_mismatch``: H1 and filename share almost no characters
        (suggests LLM drifted to a different topic).
    """
    issues: list[str] = []
    for namespace in namespaces:
        for path, _kinds in iter_h1_violations(wiki, namespace):
            for kind, detail in _kinds:
                issues.append(f"{kind}: {namespace}/{path.name} {detail}")
    return issues


def iter_h1_violations(wiki: Path, namespace: str):
    """Yield ``(path, [(kind, detail), ...])`` for each problematic page.

    Public iterator used both by lint reports and by ``openkb compact --fix-h1``
    to drive in-place repairs.
    """
    cjk_pattern = re.compile(r"[一-鿿]")
    ns_dir = wiki / namespace
    if not ns_dir.is_dir():
        return
    for path in sorted(ns_dir.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        body = raw
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            if end != -1:
                body = raw[end + 4:].lstrip("\n")
        h1_text = ""
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("# ") and not s.startswith("## "):
                h1_text = s[2:].strip()
                break
        stem = path.stem
        kinds: list[tuple[str, str]] = []
        if not h1_text:
            kinds.append(("missing_h1", ""))
        elif h1_text == stem and not cjk_pattern.search(h1_text):
            kinds.append(("h1_is_english_slug", f"(H1={h1_text!r})"))
        elif _H1_NOISE_PREFIX_RE.match(h1_text):
            kinds.append(("h1_prefix_noise", f"(H1={h1_text!r})"))
        elif cjk_pattern.search(h1_text) and cjk_pattern.search(stem):
            h1_main = re.split(r"[：:—\-\(（]", h1_text, 1)[0].strip()
            stem_main = re.split(r"[：:—\-\(（]", stem, 1)[0].strip()
            sim = max(
                _h1_jaccard(_h1_bigram_set(stem), _h1_bigram_set(h1_text)),
                _h1_jaccard(_h1_bigram_set(stem_main), _h1_bigram_set(h1_text)),
                _h1_jaccard(_h1_bigram_set(stem), _h1_bigram_set(h1_main)),
            )
            if sim < 0.25:
                kinds.append(("h1_mismatch", f"(H1={h1_text!r}, sim={sim:.2f})"))
        if kinds:
            yield path, kinds


def apply_safe_h1_fix(path: Path) -> bool:
    """Apply safe H1 repairs in place. Returns True if mutated.

    Safe = only repairs ``missing_h1`` (prepend ``# {stem}``) and
    ``h1_prefix_noise`` (strip the ``概念：`` / ``Concept:`` prefix). Skipped
    for ``h1_mismatch`` (too risky to overwrite blindly) and
    ``h1_is_english_slug`` (requires a Chinese title that doesn't exist yet).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False
    body = raw
    fm_block = ""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            fm_block = raw[: end + 4]
            body = raw[end + 4:].lstrip("\n")

    lines = body.splitlines()
    h1_idx = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            h1_idx = i
            break

    changed = False
    if h1_idx >= 0:
        h1_text = lines[h1_idx].strip()[2:].strip()
        cleaned = _H1_NOISE_PREFIX_RE.sub("", h1_text).strip()
        if cleaned and cleaned != h1_text:
            lines[h1_idx] = f"# {cleaned}"
            changed = True
    else:
        lines = [f"# {path.stem}", "", *lines]
        changed = True

    if not changed:
        return False
    new_body = "\n".join(lines)
    if raw.endswith("\n"):
        new_body += "\n"
    if fm_block:
        new_raw = fm_block.rstrip("\n") + "\n" + new_body
    else:
        new_raw = new_body
    path.write_text(new_raw, encoding="utf-8")
    return True


def find_company_alias_misuse(kb_dir: Path) -> list[str]:
    """Report registered company aliases that appear under concepts/ or industries/.

    Aliases come from the entity registry. A registered company alias used as
    a concept/industry page slug or H1 indicates the compiler missed a
    duplication or the page predates the registry entry.
    """
    try:
        from openkb.entity_registry import EntityRegistry, alias_key
    except Exception:
        return []
    registry = EntityRegistry.load(kb_dir)
    company_keys: dict[str, str] = {}
    for record in registry.records:
        if record.entity_type != "company":
            continue
        for name in record.all_names():
            key = alias_key(name)
            if key:
                company_keys.setdefault(key, record.canonical_id)
    if not company_keys:
        return []
    wiki = kb_dir / "wiki"
    issues: list[str] = []
    for sub in ("concepts", "industries"):
        ns_dir = wiki / sub
        if not ns_dir.exists():
            continue
        for path in sorted(ns_dir.glob("*.md")):
            stem_key = alias_key(path.stem)
            text = _read_md(path)
            h1_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
            h1 = h1_match.group(1).strip() if h1_match else ""
            h1_key = alias_key(h1)
            matched_id = company_keys.get(stem_key) or company_keys.get(h1_key)
            if matched_id:
                issues.append(
                    f"{sub}/{path.stem}.md is a registered company alias of "
                    f"`{matched_id}` — move to companies/ or merge"
                )
    return issues


def find_cross_listed_duplicates(kb_dir: Path) -> list[str]:
    """Report multiple wiki/companies/ pages that resolve to one canonical record."""
    try:
        from openkb.entity_registry import EntityRegistry, alias_key
    except Exception:
        return []
    registry = EntityRegistry.load(kb_dir)
    if not registry.records:
        return []
    companies_dir = kb_dir / "wiki" / "companies"
    if not companies_dir.exists():
        return []
    by_canonical: dict[str, list[str]] = {}
    for path in sorted(companies_dir.glob("*.md")):
        resolved = registry.resolve(path.stem, namespace_hint="company")
        if resolved is None:
            text = _read_md(path)
            h1_match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
            if h1_match:
                resolved = registry.resolve(h1_match.group(1).strip(), namespace_hint="company")
        if resolved is None:
            continue
        by_canonical.setdefault(resolved.canonical_id, []).append(path.stem)
    issues: list[str] = []
    for canonical_id, stems in by_canonical.items():
        if len(stems) > 1:
            issues.append(
                f"canonical `{canonical_id}` is split across pages: "
                + ", ".join(f"companies/{s}.md" for s in stems)
            )
    return issues


def find_unregistered_company_hits(kb_dir: Path) -> list[str]:
    """Surface review hints recorded by the compiler hot-path."""
    review_path = kb_dir / ".openkb" / "entity_registry" / "resolution" / "compile_unmatched_companies.json"
    if not review_path.exists():
        return []
    import json as _json
    try:
        data = _json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    issues: list[str] = []
    for record in data:
        if not isinstance(record, dict):
            continue
        surface = record.get("surface") or ""
        canonical = record.get("proposed_canonical") or ""
        symbol = record.get("xueqiu_symbol") or ""
        market = record.get("market") or ""
        if not surface or not symbol:
            continue
        issues.append(
            f"`{surface}` matches {market} {symbol} ({canonical}); add to registry "
            "via `openkb entity import-akshare`"
        )
    return issues


def run_structural_lint(kb_dir: Path) -> str:
    """Run all structural lint checks and return a formatted Markdown report.

    Args:
        kb_dir: Root of the knowledge base (contains wiki/ and raw/).

    Returns:
        Formatted Markdown string with lint results.
    """
    wiki = kb_dir / "wiki"
    raw = kb_dir / "raw"

    broken = find_broken_links(wiki)
    orphans = find_orphans(wiki)
    missing = find_missing_entries(raw, wiki)
    incomplete = find_incomplete_entries(raw, wiki)
    sync_issues = check_index_sync(wiki)
    investment_issues = find_investment_quality_issues(wiki)
    h1_issues = find_h1_issues(wiki)
    duplicate_clusters = find_concept_duplicate_clusters(kb_dir)
    alias_misuse = find_company_alias_misuse(kb_dir)
    cross_listed = find_cross_listed_duplicates(kb_dir)
    unregistered_hits = find_unregistered_company_hits(kb_dir)

    lines = ["## Structural Lint Report\n"]

    # Broken links
    lines.append(f"### Broken Links ({len(broken)})")
    if broken:
        for issue in broken:
            lines.append(f"- {issue}")
    else:
        lines.append("No broken links found.")
    lines.append("")

    # Orphans
    lines.append(f"### Orphaned Pages ({len(orphans)})")
    if orphans:
        for page in orphans:
            lines.append(f"- {page}")
    else:
        lines.append("No orphaned pages found.")
    lines.append("")

    # Missing entries
    lines.append(f"### Raw Files Without Wiki Entry ({len(missing)})")
    if missing:
        for name in missing:
            lines.append(f"- {name}")
    else:
        lines.append("All raw files have wiki entries.")
    lines.append("")

    lines.append(f"### Incomplete Wiki Entries ({len(incomplete)})")
    if incomplete:
        for name in incomplete:
            lines.append(f"- {name}")
    else:
        lines.append("No converted source-only files found.")
    lines.append("")

    # Index sync
    lines.append(f"### Index Sync Issues ({len(sync_issues)})")
    if sync_issues:
        for issue in sync_issues:
            lines.append(f"- {issue}")
    else:
        lines.append("Index is in sync.")
    lines.append("")

    # Investment-specific quality
    lines.append(f"### Investment KB Quality ({len(investment_issues)})")
    if investment_issues:
        for issue in investment_issues:
            lines.append(f"- {issue}")
    else:
        lines.append("No investment-specific quality issues found.")
    lines.append("")

    # H1 / filename consistency
    lines.append(f"### H1 / Filename Issues ({len(h1_issues)})")
    if h1_issues:
        for issue in h1_issues[:50]:
            lines.append(f"- {issue}")
        if len(h1_issues) > 50:
            lines.append(f"- ... and {len(h1_issues) - 50} more (run `python scripts/audit_concept_h1.py <kb>` for the full report)")
    else:
        lines.append("All H1 headings look healthy.")
    lines.append("")

    # Duplicate concept clusters (mergeable)
    lines.append(f"### Duplicate Concept Clusters ({len(duplicate_clusters)})")
    if duplicate_clusters:
        for issue in duplicate_clusters:
            lines.append(f"- {issue}")
        lines.append("")
        lines.append(
            "_Run `openkb merge-concepts` to review or `openkb merge-concepts --apply` to merge._"
        )
    else:
        lines.append("No duplicate concept clusters detected.")

    lines.append("")
    lines.append(f"### Registered Company Alias Misuse ({len(alias_misuse)})")
    if alias_misuse:
        for issue in alias_misuse:
            lines.append(f"- {issue}")
    else:
        lines.append("No registered company aliases misplaced under concepts/ or industries/.")

    lines.append("")
    lines.append(f"### Cross-Listed Company Page Splits ({len(cross_listed)})")
    if cross_listed:
        for issue in cross_listed:
            lines.append(f"- {issue}")
    else:
        lines.append("No cross-listing splits detected.")

    lines.append("")
    lines.append(f"### Unregistered Company Hits (compiler hot-path) ({len(unregistered_hits)})")
    if unregistered_hits:
        for issue in unregistered_hits:
            lines.append(f"- {issue}")
    else:
        lines.append("No unregistered company hits recorded.")

    return "\n".join(lines)
