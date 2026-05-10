from __future__ import annotations

import asyncio
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from _runtime import (
    AUTO_CREATE_DIRS,
    WIKILINK_RE,
    append_log,
    brief_from_text,
    bootstrap_openkb_repo_path,
    draft_page,
    emit_json,
    ensure_index_entry,
    extract_sources_field,
    git_status,
    link_for_page,
    load_evidence_map,
    markdown_pages,
    normalize_wikilink_target,
    page_id_map,
    read_text,
    relative_page_id,
    report_timestamp,
    resolve_kb,
    safe_markdown_path,
    slugify,
    title_from_text,
    wikilinks,
    wiki_root,
    write_text,
)

bootstrap_openkb_repo_path()


TICKER_RE = re.compile(r"\b\d{4,6}\.(?:tw|two|hk|ss|sz|ks|kq)\b", re.IGNORECASE)
COMPANY_SIGNAL_RE = re.compile(
    r"target\s+price|investment\s+rating|rating\s*[:：]|ticker|"
    r"\u76ee\u6807\u4ef7|\u6295\u8d44\u8bc4\u7ea7|\u8bc4\u7ea7[:\uff1a]|"
    r"\u80a1\u7968\u4ee3\u7801|\u8bc1\u5238\u4ee3\u7801",
    re.IGNORECASE,
)
COMPANY_HINT_RE = re.compile(
    r"company|supplier|foundry|semiconductor\s+company|listed\s+company|"
    r"\u516c\u53f8|\u5382\u5546|\u4f9b\u5e94\u5546|\u9f99\u5934|"
    r"\u4ee3\u5de5|\u4e0a\u5e02\u516c\u53f8|\u80a1\u7968",
    re.IGNORECASE,
)


def issue(kind: str, severity: str, message: str, path: str = "", evidence: str = "") -> dict[str, str]:
    return {"kind": kind, "severity": severity, "message": message, "path": path, "evidence": evidence}


def fix(action: str, path: str = "", title: str = "", reason: str = "", **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"action": action, "path": path, "title": title, "reason": reason, "safe": True}
    item.update(extra)
    return item


def canonical_heading(text: str, fallback: str) -> str:
    title = title_from_text(text, fallback).strip()
    return title or fallback


def concept_prefix(stem: str) -> str:
    for separator in ("--", "\u2014", "\uff1a", ":", "\uff0d", "-"):
        if separator not in stem:
            continue
        prefix = stem.split(separator, 1)[0].strip()
        if len(prefix) >= 2:
            return prefix
    return ""


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def semantic_terms(value: str) -> set[str]:
    terms: set[str] = set()
    for block in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(block) <= 2:
            terms.add(block)
        else:
            terms.update(block[index : index + 2] for index in range(len(block) - 1))
            terms.update(block[index : index + 3] for index in range(len(block) - 2))
    for token in re.findall(r"[a-z0-9][a-z0-9_.%+-]*", value.lower()):
        if len(token) >= 3:
            terms.add(token)
    return terms


def text_similarity(left: str, right: str) -> float:
    left_terms = semantic_terms(left[:6000])
    right_terms = semantic_terms(right[:6000])
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def detect_duplicate_concepts(wiki: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Find duplicate or title-expanded concept pages.

    This is semantic lint, not an automatic merge. The output is a manual-review
    worklist because deleting, redirecting, or merging durable pages needs human
    judgement.
    """
    concepts_dir = wiki / "concepts"
    if not concepts_dir.exists():
        return [], []

    pages: list[dict[str, Any]] = []
    for path in sorted(concepts_dir.glob("*.md")):
        text = read_text(path)
        stem = path.stem
        pages.append(
            {
                "path": path.relative_to(wiki).as_posix(),
                "stem": stem,
                "title": canonical_heading(text, stem),
                "brief": brief_from_text(text),
                "sources": extract_sources_field(text),
                "text": text,
            }
        )

    groups: dict[str, dict[str, Any]] = {}

    def add_group(reason: str, members: list[dict[str, Any]], confidence: str) -> None:
        unique = {member["path"]: member for member in members}
        if len(unique) < 2:
            return
        sorted_members = sorted(unique.values(), key=lambda item: (len(item["stem"]), item["path"]))
        group_key = "|".join(item["path"] for item in sorted_members)
        if group_key in groups:
            groups[group_key]["reasons"].append(reason)
            if confidence == "high":
                groups[group_key]["confidence"] = "high"
            return
        groups[group_key] = {
            "action": "review_duplicate_concept_group",
            "kind": "duplicate_concept_candidate",
            "confidence": confidence,
            "primary_candidate": sorted_members[0]["path"],
            "members": [
                {
                    "path": item["path"],
                    "title": item["title"],
                    "brief": item["brief"],
                    "sources": item["sources"],
                }
                for item in sorted_members
            ],
            "reasons": [reason],
            "recommended_action": "manual_merge_review",
            "safe": False,
        }

    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        key = normalized_name(page["title"])
        if key:
            by_title[key].append(page)
    for members in by_title.values():
        if len(members) > 1:
            add_group("same H1 title", members, "high")

    by_stem = {page["stem"]: page for page in pages}
    by_norm_stem = {normalized_name(page["stem"]): page for page in pages if normalized_name(page["stem"])}
    for page in pages:
        prefix = concept_prefix(page["stem"])
        if not prefix:
            continue
        base = by_stem.get(prefix) or by_norm_stem.get(normalized_name(prefix))
        if base and base["path"] != page["path"]:
            add_group(f"long filename expands existing concept '{prefix}'", [base, page], "high")

    for index, left in enumerate(pages):
        for right in pages[index + 1 :]:
            source_overlap = bool(set(left["sources"]) & set(right["sources"]))
            name_related = (
                normalized_name(left["stem"]) and normalized_name(left["stem"]) in normalized_name(right["stem"])
            ) or (
                normalized_name(right["stem"]) and normalized_name(right["stem"]) in normalized_name(left["stem"])
            )
            if not source_overlap and not name_related:
                continue
            similarity = text_similarity(
                f"{left['title']}\n{left['brief']}\n{left['text']}",
                f"{right['title']}\n{right['brief']}\n{right['text']}",
            )
            if similarity >= 0.30:
                confidence = "high" if similarity >= 0.45 or name_related else "medium"
                add_group(f"text/source similarity {similarity:.2f}", [left, right], confidence)

    duplicate_groups = sorted(groups.values(), key=lambda item: (item["primary_candidate"], len(item["members"])))
    duplicate_issues = [
        issue(
            "duplicate_concept_candidate",
            "high" if group["confidence"] == "high" else "medium",
            "Potential duplicate concept pages; review and merge manually.",
            ", ".join(member["path"] for member in group["members"]),
            "; ".join(group["reasons"]),
        )
        for group in duplicate_groups
    ]
    return duplicate_issues, duplicate_groups


def replacement_inner(original_inner: str, resolved_target: str) -> str:
    alias = ""
    suffix = ""
    target_part = original_inner
    if "|" in target_part:
        target_part, alias = target_part.split("|", 1)
        alias = "|" + alias
    if "#" in target_part:
        _, suffix = target_part.split("#", 1)
        suffix = "#" + suffix
    return f"{resolved_target}{suffix}{alias}"


def resolve_missing_target(target: str, pages: dict[str, Path], wiki: Path) -> str | None:
    normalized = target.casefold()
    variants = {
        normalized,
        normalized.replace(" ", "_"),
        normalized.replace(" ", "-"),
        normalized.replace("-", "_"),
        normalized.replace("_", "-"),
    }
    for page_id, path in pages.items():
        rel = relative_page_id(wiki, path)
        candidates = {
            page_id.casefold(),
            rel.casefold(),
            Path(page_id).stem.casefold(),
            page_id.casefold().replace("-", "_"),
            page_id.casefold().replace("_", "-"),
        }
        if variants & candidates:
            return rel
    return None


def create_draft_if_safe(wiki: Path, page_path: str, title: str, reason: str, fixes: list[dict], changes: list[str]) -> bool:
    resolved = safe_markdown_path(wiki, page_path)
    if resolved is None:
        return False
    full, rel = resolved
    if full.exists():
        return False
    page_type = rel.split("/", 1)[0]
    write_text(full, draft_page(title or Path(rel).stem, page_type, reason))
    ensure_index_entry(wiki, rel, title or Path(rel).stem, "lint draft")
    fixes.append(fix("create_draft_page", rel, title or Path(rel).stem, reason, page_type=page_type))
    changes.append(rel)
    return True


def append_source_evidence_todo(wiki: Path, rel: str, text: str, fixes: list[dict], changes: list[str]) -> str:
    return text


def append_review_note(wiki: Path, rel: str, text: str, note: str, fixes: list[dict], changes: list[str]) -> str:
    if note in text:
        return text
    new_text = text.rstrip() + f"\n\n## Lint Review\n{note}\n"
    write_text(wiki / rel, new_text)
    fixes.append(fix("append_review_note", rel, reason=note))
    changes.append(rel)
    return new_text


def _run_system_lint(kb_root: Path, *, fix: bool) -> Path | None:
    bootstrap_openkb_repo_path()
    try:
        from openkb.cli import run_lint
    except Exception:
        return None

    try:
        return asyncio.run(run_lint(kb_root, fix=fix))
    except Exception:
        return None


def build_lint(kb: str, apply_safe: bool, create_drafts: bool = False, add_todos: bool = False) -> dict:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}

    wiki = wiki_root(kb_root)
    timestamp = report_timestamp()
    git_before = git_status(kb_root)
    pages = page_id_map(wiki)
    issues: list[dict[str, str]] = []
    manual_review: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []
    changes: list[str] = []

    duplicate_issues, duplicate_groups = detect_duplicate_concepts(wiki)
    issues.extend(duplicate_issues)
    manual_review.extend(duplicate_groups)

    scan_pages = markdown_pages(wiki, content_only=False)
    index_text = read_text(wiki / "index.md")
    lower_pages = {key.casefold(): value for key, value in pages.items()}
    incoming: set[str] = set()
    outgoing: dict[str, set[str]] = {}

    for path in scan_pages:
        rel = path.relative_to(wiki).as_posix()
        text = read_text(path)
        links = set(wikilinks(text))
        outgoing[rel[:-3] if rel.endswith(".md") else rel] = links
        for target in links:
            incoming.add(target)
            incoming.add(Path(target).stem)

        changed_text = text
        for match in WIKILINK_RE.finditer(text):
            inner = match.group(1)
            target = normalize_wikilink_target(inner)
            if not target:
                continue
            if ".." in Path(target).parts or target.startswith("/") or ":" in target:
                issues.append(issue("path_traversal", "high", f"Unsafe wikilink target [[{target}]]", rel))
                manual_review.append({"action": "review_unsafe_link", "path": rel, "target": target, "safe": False})
                continue
            if target in pages or target.casefold() in lower_pages:
                continue
            resolved = resolve_missing_target(target, pages, wiki)
            if resolved and apply_safe:
                new_inner = replacement_inner(inner, resolved)
                changed_text = changed_text.replace(f"[[{inner}]]", f"[[{new_inner}]]")
                fixes.append(fix("rewrite_wikilink", rel, reason=f"Resolved [[{target}]] to [[{resolved}]].", original=inner, resolved=resolved))
                changes.append(rel)
            elif resolved:
                issues.append(issue("broken_link_resolvable", "medium", f"Broken wikilink can resolve to [[{resolved}]]", rel, target))
            elif target.split("/", 1)[0] in AUTO_CREATE_DIRS:
                page_title = Path(target).stem.replace("_", " ").replace("-", " ").strip().title() or target
                issues.append(issue("missing_link_target", "medium", f"Missing page target [[{target}]]", rel))
                if apply_safe and create_drafts:
                    create_draft_if_safe(wiki, f"{target}.md", page_title, f"Linked from {rel}", fixes, changes)
                else:
                    manual_review.append(
                        {
                            "action": "review_missing_page_target",
                            "path": rel,
                            "target": target,
                            "suggested_draft_path": f"{target}.md",
                            "reason": f"Linked from {rel}",
                            "safe": False,
                        }
                    )
            else:
                issues.append(issue("broken_link", "medium", f"Broken wikilink [[{target}]]", rel))
                manual_review.append({"action": "review_broken_link", "path": rel, "target": target, "safe": False})
        if changed_text != text and apply_safe:
            write_text(path, changed_text)

    content_pages = [path for path in markdown_pages(wiki, content_only=True) if path.name != "index.md"]
    for path in content_pages:
        rel = path.relative_to(wiki).as_posix()
        page_id = rel[:-3] if rel.endswith(".md") else rel
        text = read_text(path)
        category = rel.split("/", 1)[0] if "/" in rel else ""
        title = title_from_text(text, path.stem)
        brief = brief_from_text(text)
        links = outgoing.get(page_id, set())

        if category in {"summaries", "companies", "industries", "concepts", "explorations"} and f"[[{page_id}]]" not in index_text and page_id not in index_text:
            issues.append(issue("index_drift", "medium", f"{rel} is not mentioned in index.md", rel))
            if apply_safe and ensure_index_entry(wiki, rel, title, "lint indexed"):
                fixes.append(fix("ensure_index_entry", rel, title, "Page was missing from index.md."))
                changes.append("index.md")

        if page_id != "index" and not links and page_id not in incoming and Path(page_id).stem not in incoming:
            issues.append(issue("orphan_page", "low", "Page has no incoming or outgoing wikilinks.", rel))

        if category in {"companies", "industries", "concepts"} and "## Source Evidence" not in text:
            issues.append(issue("missing_source_evidence", "medium", "Important page lacks a Source Evidence section.", rel))
            if apply_safe and add_todos:
                text = append_source_evidence_todo(wiki, rel, text, fixes, changes)

        if category in {"companies", "industries", "concepts"} and not brief:
            issues.append(issue("missing_brief", "low", "Page lacks a useful brief/frontmatter or opening summary.", rel))

        if title.casefold() in {"summary", "report", "notes", "overview"}:
            issues.append(issue("generic_title", "low", f"Page title is too generic: {title}", rel))

        for source in extract_sources_field(text):
            source_rel = source if source.endswith(".md") or source.endswith(".json") else f"{source}.md"
            if not (wiki / source_rel).exists():
                issues.append(issue("missing_source_reference", "medium", f"Frontmatter source does not exist: {source}", rel))

        if category == "concepts":
            intro = text[:1200]
            looks_company = bool((TICKER_RE.search(intro) or COMPANY_SIGNAL_RE.search(intro)) and COMPANY_HINT_RE.search(intro))
            if looks_company:
                issues.append(issue("company_boundary", "high", "Concept page appears to describe a company-specific investment object.", rel))
                company_slug = slugify(path.stem, "company")
                company_rel = f"companies/{company_slug}.md"
                if apply_safe and create_drafts:
                    created = create_draft_if_safe(wiki, company_rel, title, f"Company-like concept page: {rel}", fixes, changes)
                    note = f"This page may be company-specific. Review whether durable company content should move to [[{link_for_page(company_rel)}]]."
                    append_review_note(wiki, rel, read_text(path), note, fixes, changes)
                    if not created:
                        manual_review.append({"action": "review_company_boundary", "path": rel, "target": company_rel, "safe": False})
                else:
                    manual_review.append(
                        {
                            "action": "review_company_boundary",
                            "path": rel,
                            "target": company_rel,
                            "reason": "Potential company-like concept; draft creation requires --create-drafts.",
                            "safe": False,
                        }
                    )

    raw_dir = kb_root / "raw"
    source_stems = set()
    source_dir = wiki / "sources"
    if source_dir.exists():
        source_stems |= {path.stem for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in {".md", ".json"}}
    summary_stems = {path.stem for path in (wiki / "summaries").glob("*.md")} if (wiki / "summaries").exists() else set()
    if raw_dir.exists():
        for raw in sorted(raw_dir.iterdir()):
            if not raw.is_file():
                continue
            if raw.stem not in source_stems and raw.stem not in summary_stems:
                issues.append(issue("raw_without_wiki_entry", "medium", "Raw file has no source or summary wiki entry.", raw.relative_to(kb_root).as_posix()))
            elif raw.stem in source_stems and raw.stem not in summary_stems:
                issues.append(issue("source_without_summary", "medium", "Converted source exists but summary is missing.", raw.relative_to(kb_root).as_posix()))

    evidence = load_evidence_map(wiki)
    for key, entries in evidence.items():
        page_path = key if key.endswith(".md") else f"{key}.md"
        if not (wiki / page_path).exists():
            issues.append(issue("evidence_map_drift", "medium", f"Evidence map key points to a missing page: {key}", "evidence_map.json"))
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    source = str(entry.get("source") or "")
                    if source and not (wiki / source).exists():
                        issues.append(issue("evidence_source_missing", "medium", f"Evidence source missing: {source}", "evidence_map.json"))

    log_text = read_text(wiki / "log.md")
    query_count = len(re.findall(r"\]\s+query\s+\|", log_text))
    exploration_count = len(list((wiki / "explorations").glob("*.md"))) if (wiki / "explorations").exists() else 0
    if query_count >= 5 and exploration_count == 0:
        issues.append(issue("query_compounding_gap", "low", "Multiple queries are logged but no explorations are saved.", "log.md"))

    system_report_path = _run_system_lint(kb_root, fix=apply_safe)
    lint_backend = "system" if system_report_path is not None else "standalone"
    if system_report_path is None:
        warnings.append("System openkb lint unavailable; using standalone skill scanner.")
    report_path = system_report_path or (wiki / "reports" / f"lint_{timestamp}.md")
    json_path = wiki / "reports" / f"lint_{timestamp}.json"
    report_rel = report_path.relative_to(wiki).as_posix()
    json_rel = json_path.relative_to(wiki).as_posix()
    if system_report_path is None:
        report = format_report(
            timestamp,
            apply_safe,
            create_drafts,
            add_todos,
            git_before,
            issues,
            fixes,
            manual_review,
            duplicate_groups,
            changes,
            warnings,
        )
        write_text(report_path, report)
        if apply_safe:
            append_log(wiki, "lint", f"report -> {report_path.name}; safe_fixes={len(fixes)}")
            changes.append("log.md")
    else:
        changes.append("log.md")
    changes.extend([report_rel, json_rel])
    payload = {
        "ok": True,
        "timestamp": timestamp,
        "kb_root": str(kb_root),
        "wiki_root": str(wiki),
        "lint_backend": lint_backend,
        "apply_safe": apply_safe,
        "create_drafts": create_drafts,
        "add_todos": add_todos,
        "report": report_rel,
        "json_report": json_rel,
        "issues": issues,
        "fix_plan": fixes,
        "manual_review": manual_review,
        "duplicate_concept_groups": duplicate_groups,
        "changed_files": sorted(set(changes)),
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
        "warnings": warnings,
    }
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def format_report(
    timestamp: str,
    apply_safe: bool,
    create_drafts: bool,
    add_todos: bool,
    git_before: dict[str, Any],
    issues: list[dict[str, str]],
    fixes: list[dict[str, Any]],
    manual_review: list[dict[str, Any]],
    duplicate_groups: list[dict[str, Any]],
    changes: list[str],
    warnings: list[str],
) -> str:
    lines = [f"# OpenKB Lint Report - {timestamp}", ""]
    lines.append(f"- Mode: {'apply-safe' if apply_safe else 'report-only'}")
    lines.append(f"- Draft creation: {'enabled' if create_drafts else 'disabled'}")
    lines.append(f"- Evidence scaffolding: {'deprecated flag ignored' if add_todos else 'disabled'}")
    lines.append(f"- Issues: {len(issues)}")
    lines.append(f"- Safe fixes {'applied' if apply_safe else 'planned'}: {len(fixes)}")
    lines.append(f"- Manual review items: {len(manual_review)}")
    lines.append(f"- Duplicate concept groups: {len(duplicate_groups)}")
    lines.append(f"- Git repo: {bool(git_before.get('is_git_repo'))}")
    if git_before.get("status"):
        lines.append("- Git status before:")
        lines.extend(f"  - `{line}`" for line in str(git_before["status"]).splitlines())
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    lines.append("## Issues")
    if not issues:
        lines.append("No issues found.")
    else:
        for item in issues:
            loc = f" `{item['path']}`" if item.get("path") else ""
            evidence = f" - {item['evidence']}" if item.get("evidence") else ""
            lines.append(f"- [{item['severity']}] {item['kind']}{loc}: {item['message']}{evidence}")
    if duplicate_groups:
        lines.append("")
        lines.append("## Duplicate Concept Candidates")
        for group in duplicate_groups:
            members = ", ".join(f"`{member['path']}`" for member in group["members"])
            primary = group.get("primary_candidate", "")
            reason = "; ".join(group.get("reasons", []))
            lines.append(f"- Primary: `{primary}`")
            lines.append(f"  Members: {members}")
            lines.append(f"  Reason: {reason}")
    lines.append("")
    lines.append("## Safe Fixes")
    if not fixes:
        lines.append("No safe fixes were applied or planned.")
    else:
        for item in fixes:
            action = item.get("action", "fix")
            path = item.get("path", "")
            reason = item.get("reason", "")
            lines.append(f"- {action} `{path}` - {reason}")
    lines.append("")
    lines.append("## Manual Review")
    if not manual_review:
        lines.append("No manual-review items.")
    else:
        for item in manual_review:
            lines.append(f"- `{item.get('action')}` {json.dumps(item, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Changed Files")
    if sorted(set(changes)):
        lines.extend(f"- `{path}`" for path in sorted(set(changes)))
    else:
        lines.append("No wiki content files changed.")
    lines.append("")
    lines.append("## Guardrails")
    lines.append("- raw/ and sources/ are report-only surfaces; this skill does not overwrite original documents.")
    lines.append("- Draft-page creation is disabled unless --create-drafts is passed.")
    lines.append("- Source-evidence scaffolding is deprecated; missing evidence is report-only.")
    lines.append("- Deletions, merges, and contradiction rewrites are manual-review only.")
    lines.append("- Secrets are not printed; .env contents are never included.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint an OpenKB runtime wiki and optionally apply conservative safe fixes.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--apply-safe", dest="apply_safe", action="store_true", default=True, help="Apply conservative structural safe-fixes. Default.")
    parser.add_argument("--report-only", dest="apply_safe", action="store_false", help="Only write lint reports; do not change wiki content pages or log.")
    parser.add_argument("--create-drafts", action="store_true", help="Allow draft-page creation for missing pages and company-like pages.")
    parser.add_argument("--add-todos", action="store_true", help="Deprecated no-op; missing source evidence remains report-only.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = build_lint(args.kb, apply_safe=args.apply_safe, create_drafts=args.create_drafts, add_todos=args.add_todos)
    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Lint failed."))
        return
    print(f"Report: {data['report']}")
    print(f"Issues: {len(data['issues'])}")
    print(f"Duplicate concept groups: {len(data.get('duplicate_concept_groups', []))}")
    print(f"Safe fixes: {len(data['fix_plan'])}")
    print(f"Manual review: {len(data['manual_review'])}")


if __name__ == "__main__":
    main()
