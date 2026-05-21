"""Concept page de-duplication and merging.

Given a wiki directory under a KB, this module:

  * Builds normalized name variants over each concept page's slug and H1.
  * Pairs up only high-confidence aliases whose normalized names match.
  * Forms equivalence classes (Union-Find) so transitive variants land in
    the same merge group.
  * For each cluster of size ≥ 2:
      - Picks a canonical slug (prefer shorter stem, then more recent ``modified``).
      - Merges ``sources:`` frontmatter (union, preserving order).
      - Appends de-duplicated source-evidence sections from siblings.
      - Rewrites every cross-reference ``[[concepts/X]]`` / ``[[X]]`` /
        ``[X](concepts/X.md)`` / ``concepts/X`` mention across the whole wiki
        to the canonical slug.
      - Deletes the sibling files.
      - Patches ``index.md`` so dead links don't linger.

Usage (called from CLI):

    from openkb.concept_merge import propose_merges, apply_merges
    proposals = propose_merges(kb_dir)   # dry-run analysis
    apply_merges(kb_dir, proposals)      # destructive: rewrites files & deletes

This module deliberately avoids LLM calls so a user can run it offline and
inspect the proposal before applying. The semantic matching strategy is the
same lightweight char-unigram approach used by the live planner, so what the
planner now prevents going forward, this module can clean up retrospectively.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


_CJK_LO, _CJK_HI = "㐀", "鿿"


def _unigrams(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return {c for c in text if c.isalnum() or _CJK_LO <= c <= _CJK_HI}


def _normalized_name(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(c for c in text if c.isalnum() or _CJK_LO <= c <= _CJK_HI)


def _strip_parenthetical(text: str) -> str:
    return re.sub(r"[\(（][^\)）]*[\)）]", "", text).strip()


def _name_variants(page: "ConceptPage") -> set[str]:
    """Return conservative normalized aliases for a concept page.

    The lint merge proposal path must be high precision because its output can
    drive destructive merges. Character overlap alone produces many false
    positives in Chinese investment KBs (for example 上行风险/下行风险 or
    PCE/CPI), so only exact normalized slug/H1 aliases are considered.
    """
    variants: set[str] = set()
    for value in (page.slug, page.title, _strip_parenthetical(page.title)):
        normalized = _normalized_name(value)
        if len(normalized) >= 3:
            variants.add(normalized)
    return variants


def _parse_concept_page(path: Path) -> tuple[list[str], str, str]:
    """Return ``(sources, brief, body)`` from a concept page."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [], "", ""
    sources: list[str] = []
    brief = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            body = text[end + 4:].lstrip("\n")
            for line in fm.split("\n"):
                if line.startswith("sources:"):
                    payload = line[len("sources:"):].strip()
                    if payload.startswith("["):
                        payload = payload.strip("[]")
                        sources = [s.strip() for s in payload.split(",") if s.strip()]
                    else:
                        sources = [payload] if payload else []
                elif line.startswith("brief:"):
                    brief = line[len("brief:"):].strip()
    return sources, brief, body


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip()
    return ""


@dataclass
class ConceptPage:
    slug: str
    path: Path
    sources: list[str]
    brief: str
    title: str
    body: str
    tight_sig: set[str] = field(default_factory=set)
    wide_sig: set[str] = field(default_factory=set)

    @property
    def stem_len(self) -> int:
        return len(self.slug)

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


def _load_concepts(wiki_dir: Path) -> list[ConceptPage]:
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.is_dir():
        return []
    pages: list[ConceptPage] = []
    for p in sorted(concepts_dir.glob("*.md")):
        sources, brief, body = _parse_concept_page(p)
        title = _first_h1(body) or p.stem
        tight = _unigrams(f"{p.stem} {title}")
        wide = tight | _unigrams(brief)
        pages.append(ConceptPage(
            slug=p.stem, path=p, sources=sources, brief=brief,
            title=title, body=body, tight_sig=tight, wide_sig=wide,
        ))
    return pages


def _cjk_only(s: set[str]) -> set[str]:
    return {c for c in s if _CJK_LO <= c <= _CJK_HI}


def _similarity(a: ConceptPage, b: ConceptPage) -> float:
    """Return high-confidence alias similarity for merge proposals."""
    if _name_variants(a) & _name_variants(b):
        return 1.0
    return 0.0


class _UF:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class MergeProposal:
    canonical: str
    merged: list[str]  # all slugs in this cluster (incl. canonical)
    rationale: dict[str, float]  # slug → similarity-to-canonical
    sources_union: list[str]


def propose_merges(
    kb_dir: Path,
    *,
    tight_threshold: float = 0.55,
    wide_threshold: float = 0.45,
) -> list[MergeProposal]:
    """Return a list of merge proposals. Pure analysis — does not touch disk.

    Strategy: build a list of all near-duplicate pairs, sort by similarity
    desc, then greedily group them so each non-canonical page sits in exactly
    one cluster and *every* sibling is **directly** similar to the canonical
    (no Union-Find transitive closure — that risks merging A-B and B-C into
    one cluster even when A and C are unrelated).
    """
    wiki_dir = kb_dir / "wiki"
    pages = _load_concepts(wiki_dir)
    if not pages:
        return []

    n = len(pages)
    threshold = min(tight_threshold, wide_threshold)
    pairs: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = _similarity(pages[i], pages[j])
            if sim >= threshold:
                pairs.append((i, j, sim))
    pairs.sort(key=lambda x: -x[2])

    # Per page → its assigned canonical slug (if any).
    assigned: dict[int, int] = {}
    # canonical idx → list of sibling idxs (non-canonical members).
    clusters: dict[int, list[int]] = {}

    def _canonical_choice(a: int, b: int) -> tuple[int, int]:
        """Return (canon_idx, dup_idx) preferring shorter slug, then newer mtime."""
        pa, pb = pages[a], pages[b]
        if (pa.stem_len, -pa.mtime, pa.slug) <= (pb.stem_len, -pb.mtime, pb.slug):
            return a, b
        return b, a

    for i, j, sim in pairs:
        if i in assigned and j in assigned:
            continue
        if i in assigned:
            # j wants to join i's cluster — only allow if j is directly similar
            # to that cluster's canonical (which it is, since assigned[i] points
            # to a canonical and we are evaluating pair (i, j); however i may
            # not be the canonical itself). Skip — j becomes its own concern.
            canon_of_i = assigned[i]
            if canon_of_i == j:
                continue
            sim_to_canon = _similarity(pages[canon_of_i], pages[j])
            if sim_to_canon >= threshold:
                assigned[j] = canon_of_i
                clusters[canon_of_i].append(j)
            continue
        if j in assigned:
            canon_of_j = assigned[j]
            if canon_of_j == i:
                continue
            sim_to_canon = _similarity(pages[canon_of_j], pages[i])
            if sim_to_canon >= threshold:
                assigned[i] = canon_of_j
                clusters[canon_of_j].append(i)
            continue
        # Neither in a cluster yet — seed a new one.
        canon_idx, dup_idx = _canonical_choice(i, j)
        assigned[canon_idx] = canon_idx
        assigned[dup_idx] = canon_idx
        clusters.setdefault(canon_idx, []).append(dup_idx)

    proposals: list[MergeProposal] = []
    for canon_idx, sibs in clusters.items():
        if not sibs:
            continue
        canon_page = pages[canon_idx]
        sib_pages = [pages[s] for s in sibs]
        sources_union: list[str] = []
        seen: set[str] = set()
        for p in [canon_page, *sib_pages]:
            for s in p.sources:
                if s not in seen:
                    seen.add(s)
                    sources_union.append(s)
        rationale = {p.slug: _similarity(canon_page, p) for p in sib_pages}
        proposals.append(MergeProposal(
            canonical=canon_page.slug,
            merged=[canon_page.slug, *(p.slug for p in sib_pages)],
            rationale=rationale,
            sources_union=sources_union,
        ))
    proposals.sort(key=lambda p: (-len(p.merged), p.canonical))
    return proposals


def _frontmatter_with(sources: list[str], brief: str) -> str:
    lines = ["---", f"sources: [{', '.join(sources)}]"]
    if brief:
        lines.append(f"brief: {brief}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


_WIKILINK_RE = re.compile(r"\[\[concepts/([^\]\|]+?)(?:\|[^\]]*)?\]\]")
_BARE_WIKI_RE = re.compile(r"\[\[([^\]\|/]+?)(?:\|[^\]]*)?\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]*?)\]\(concepts/([^)]+?)\.md\)")
_PLAIN_RE = re.compile(r"concepts/([^\s\)\]]+)")


def _rewrite_concept_refs(text: str, mapping: dict[str, str]) -> str:
    """Rewrite every reference to a renamed concept slug.

    ``mapping`` maps old_slug → canonical_slug. Leaves all other text intact.
    """
    if not mapping:
        return text

    def _sub_wikilink(match: re.Match[str]) -> str:
        slug = match.group(1)
        canonical = mapping.get(slug, slug)
        return f"[[concepts/{canonical}]]"

    def _sub_bare(match: re.Match[str]) -> str:
        slug = match.group(1)
        canonical = mapping.get(slug)
        if canonical is None:
            return match.group(0)
        return f"[[concepts/{canonical}]]"

    def _sub_md(match: re.Match[str]) -> str:
        label = match.group(1)
        slug = match.group(2)
        canonical = mapping.get(slug, slug)
        return f"[{label or canonical}](concepts/{canonical}.md)"

    text = _WIKILINK_RE.sub(_sub_wikilink, text)
    text = _BARE_WIKI_RE.sub(_sub_bare, text)
    text = _MD_LINK_RE.sub(_sub_md, text)
    return text


def _merge_bodies(pages: list[ConceptPage], canonical: ConceptPage) -> str:
    """Append non-canonical bodies under '## 合并的早期版本' for traceability."""
    body = canonical.body.rstrip() + "\n"
    siblings = [p for p in pages if p.slug != canonical.slug]
    if not siblings:
        return body
    body += "\n## 合并的早期版本\n"
    for sib in siblings:
        body += f"\n### 原概念：`{sib.slug}`\n"
        if sib.brief:
            body += f"_{sib.brief}_\n\n"
        sib_body = sib.body.strip()
        # Drop the sibling's own H1 since the canonical H1 already covers it.
        sib_lines = sib_body.splitlines()
        if sib_lines and sib_lines[0].lstrip().startswith("# "):
            sib_body = "\n".join(sib_lines[1:]).strip()
        body += sib_body + "\n"
    return body


def apply_merges(kb_dir: Path, proposals: list[MergeProposal]) -> dict[str, int]:
    """Execute the merges. Returns counts dict for reporting."""
    wiki_dir = kb_dir / "wiki"
    pages = {p.slug: p for p in _load_concepts(wiki_dir)}
    rename_map: dict[str, str] = {}
    merged_files = 0
    deleted_files = 0
    rewritten_files = 0

    for proposal in proposals:
        canonical = pages.get(proposal.canonical)
        if canonical is None:
            continue
        member_pages = [pages[s] for s in proposal.merged if s in pages]
        if len(member_pages) < 2:
            continue
        new_body = _merge_bodies(member_pages, canonical)
        new_brief = canonical.brief or next((p.brief for p in member_pages if p.brief), "")
        canonical.path.write_text(
            _frontmatter_with(proposal.sources_union, new_brief) + new_body.lstrip(),
            encoding="utf-8",
        )
        merged_files += 1
        for p in member_pages:
            if p.slug == canonical.slug:
                continue
            rename_map[p.slug] = canonical.slug
            try:
                p.path.unlink()
                deleted_files += 1
            except OSError:
                pass

    # Patch every wiki file's wikilinks / md links / plain refs.
    for md_file in sorted(wiki_dir.rglob("*.md")):
        try:
            original = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        updated = _rewrite_concept_refs(original, rename_map)
        if updated != original:
            md_file.write_text(updated, encoding="utf-8")
            rewritten_files += 1

    return {
        "clusters_merged": merged_files,
        "files_deleted": deleted_files,
        "files_rewritten": rewritten_files,
    }
