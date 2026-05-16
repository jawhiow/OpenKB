from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from _runtime import bootstrap_openkb_repo_path, emit_json, git_status, resolve_kb

bootstrap_openkb_repo_path()

try:
    from openkb.agent.h1_rename import apply_h1_renames, propose_h1_renames, render_suggestions_report
    from openkb.cli import SUPPORTED_EXTENSIONS, add_single_file
    from openkb.concept_merge import apply_merges, propose_merges
    from openkb.document_ledger import backfill_document_ledger
    from openkb.kb_git import commit_kb_changes
    from openkb.lint import apply_safe_h1_fix, find_h1_issues, iter_h1_violations, run_structural_lint
    from openkb.log import append_log
    from openkb.model_pool import select_model_route
except Exception:  # pragma: no cover - used when copied without openkb installed.
    SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
    add_single_file = None  # type: ignore[assignment]
    apply_h1_renames = None  # type: ignore[assignment]
    propose_h1_renames = None  # type: ignore[assignment]
    render_suggestions_report = None  # type: ignore[assignment]
    apply_merges = None  # type: ignore[assignment]
    propose_merges = None  # type: ignore[assignment]
    backfill_document_ledger = None  # type: ignore[assignment]
    commit_kb_changes = None  # type: ignore[assignment]
    apply_safe_h1_fix = None  # type: ignore[assignment]
    find_h1_issues = None  # type: ignore[assignment]
    iter_h1_violations = None  # type: ignore[assignment]
    run_structural_lint = None  # type: ignore[assignment]
    append_log = None  # type: ignore[assignment]
    select_model_route = None  # type: ignore[assignment]


def _commit_payload(result: Any) -> dict[str, Any]:
    if result is None:
        return {"git_available": False, "committed": False, "message": "", "skipped_reason": "git helper unavailable"}
    return {
        "git_available": bool(getattr(result, "git_available", False)),
        "committed": bool(getattr(result, "committed", False)),
        "commit_hash": str(getattr(result, "commit_hash", "")),
        "message": str(getattr(result, "message", "")),
        "skipped_reason": str(getattr(result, "skipped_reason", "")),
    }


def _proposal_payload(proposal: Any) -> dict[str, Any]:
    return {
        "canonical": proposal.canonical,
        "merged": list(proposal.merged),
        "rationale": dict(proposal.rationale),
        "sources_union": list(proposal.sources_union),
    }


def backfill_ledger(kb: str) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if backfill_document_ledger is None:
        return {"ok": False, "error": "The OpenKB document ledger helper is unavailable.", "warnings": warnings}
    git_before = git_status(kb_root)
    result = backfill_document_ledger(kb_root)
    commit = None
    if result.get("added") or result.get("updated"):
        commit = commit_kb_changes(kb_root, "Backfill document ledger") if commit_kb_changes is not None else None
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "result": result,
        "commit": _commit_payload(commit),
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
        "warnings": warnings,
    }


def rebuild(kb: str, *, yes: bool = False, strict: bool = False) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if add_single_file is None:
        return {"ok": False, "error": "The OpenKB add helper is unavailable.", "warnings": warnings}
    raw_dir = kb_root / "raw"
    files = [path for path in sorted(raw_dir.rglob("*")) if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS] if raw_dir.exists() else []
    if not yes:
        return {"ok": True, "dry_run": True, "kb_root": str(kb_root), "would_rebuild": [str(path) for path in files], "warnings": warnings}

    git_before = git_status(kb_root)
    rebuilt: list[str] = []
    failed: list[dict[str, str]] = []
    for file_path in files:
        try:
            add_single_file(file_path, kb_root, force=True, strict=strict)
            rebuilt.append(str(file_path))
        except Exception as exc:
            failed.append({"path": str(file_path), "error": str(exc)})
            if strict:
                break
    return {
        "ok": not failed,
        "dry_run": False,
        "kb_root": str(kb_root),
        "rebuilt": rebuilt,
        "failed": failed,
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
        "warnings": warnings,
    }


def merge_concepts(kb: str, *, apply: bool = False, tight: float = 0.55, wide: float = 0.45) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if propose_merges is None:
        return {"ok": False, "error": "The OpenKB concept merge helper is unavailable.", "warnings": warnings}
    git_before = git_status(kb_root)
    proposals = propose_merges(kb_root, tight_threshold=tight, wide_threshold=wide)
    payload = [_proposal_payload(proposal) for proposal in proposals]
    if not apply:
        return {"ok": True, "dry_run": True, "kb_root": str(kb_root), "proposals": payload, "warnings": warnings, "git_status_before": git_before}
    if apply_merges is None:
        return {"ok": False, "error": "The OpenKB apply merge helper is unavailable.", "warnings": warnings}
    result = apply_merges(kb_root, proposals)
    if append_log is not None:
        append_log(kb_root / "wiki", "merge-concepts", f"{result['clusters_merged']} clusters, {result['files_deleted']} deletions")
    return {
        "ok": True,
        "dry_run": False,
        "kb_root": str(kb_root),
        "proposals": payload,
        "result": result,
        "warnings": warnings,
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
    }


def compact(kb: str, *, fix_h1: bool = False, apply_merges_flag: bool = False) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if find_h1_issues is None or propose_merges is None or run_structural_lint is None:
        return {"ok": False, "error": "The OpenKB compact helpers are unavailable.", "warnings": warnings}

    git_before = git_status(kb_root)
    wiki = kb_root / "wiki"
    today = datetime.now().strftime("%Y%m%d")
    h1_issues = find_h1_issues(wiki)
    proposals = propose_merges(kb_root)
    structural = run_structural_lint(kb_root)
    fixed_h1 = 0
    if fix_h1 and apply_safe_h1_fix is not None and iter_h1_violations is not None:
        for namespace in ("concepts", "companies", "industries"):
            for path, _kinds in iter_h1_violations(wiki, namespace):
                if apply_safe_h1_fix(path):
                    fixed_h1 += 1
    merge_result: dict[str, Any] | None = None
    if apply_merges_flag and apply_merges is not None:
        merge_result = apply_merges(kb_root, proposals)

    reports_dir = wiki / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"compact-{today}.md"
    total_dupes = sum(len(proposal.merged) - 1 for proposal in proposals)
    lines = [
        f"# KB Compact Report - {today}",
        "",
        f"- H1 issues: **{len(h1_issues)}**  (fixed in-place: {fixed_h1})",
        f"- Duplicate clusters: **{len(proposals)}** covering **{total_dupes}** page(s)  (merged: {(merge_result or {}).get('clusters_merged', 0)})",
        "",
        "## H1 issues",
    ]
    lines.extend(f"- {item}" for item in h1_issues[:200]) if h1_issues else lines.append("- (none)")
    lines.extend(["", "## Duplicate concept clusters"])
    if proposals:
        for proposal in proposals:
            lines.append(f"- canonical: `{proposal.canonical}`")
            for slug in proposal.merged[1:]:
                lines.append(f"  - merge: `{slug}` (sim={proposal.rationale.get(slug, 0.0):.3f})")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Structural lint", "", structural])
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if append_log is not None:
        append_log(wiki, "compact", f"h1={len(h1_issues)}/fixed={fixed_h1}, clusters={len(proposals)}/merged={(merge_result or {}).get('clusters_merged', 0)}")
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "report": report_path.relative_to(wiki).as_posix(),
        "h1_issues": h1_issues,
        "fixed_h1": fixed_h1,
        "merge_proposals": [_proposal_payload(proposal) for proposal in proposals],
        "merge_result": merge_result,
        "structural_lint": structural,
        "warnings": warnings,
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
    }


def h1_rename(kb: str, *, apply: bool = False, confidence: float = 0.7, language: str = "Chinese") -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if propose_h1_renames is None or render_suggestions_report is None or select_model_route is None:
        return {"ok": False, "error": "The OpenKB H1 rename helpers are unavailable.", "warnings": warnings}
    git_before = git_status(kb_root)
    route = select_model_route(kb_root)
    suggestions = propose_h1_renames(kb_root, model=route.model, language=language, auto_apply_threshold=confidence)
    report_path = kb_root / "wiki" / "reports" / f"h1-rename-{datetime.now().strftime('%Y%m%d')}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_suggestions_report(suggestions), encoding="utf-8")
    result = None
    if apply:
        approved = [suggestion for suggestion in suggestions if suggestion.auto_applicable]
        result = apply_h1_renames(kb_root, approved) if apply_h1_renames is not None else None
        if append_log is not None and result is not None:
            append_log(kb_root / "wiki", "h1-rename", f"rewrote_h1={len(result['h1_rewritten'])} renamed={len(result['renamed'])} errors={len(result['errors'])}")
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "model": route.model,
        "report": report_path.relative_to(kb_root / "wiki").as_posix(),
        "suggestions": [suggestion.to_dict() for suggestion in suggestions],
        "result": result,
        "warnings": warnings,
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OpenKB maintenance workflows with JSON output.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--mode", choices=("backfill-ledger", "rebuild", "compact", "merge-concepts", "h1-rename"), required=True)
    parser.add_argument("--yes", action="store_true", help="Actually run destructive rebuild operations; otherwise preview.")
    parser.add_argument("--apply", action="store_true", help="Apply merge or h1-rename suggestions instead of dry-run.")
    parser.add_argument("--fix-h1", action="store_true", help="Apply compact's safe H1 fixes.")
    parser.add_argument("--apply-merges", action="store_true", help="Apply compact concept merge proposals.")
    parser.add_argument("--strict", action="store_true", help="Stop rebuild on first failure.")
    parser.add_argument("--tight", type=float, default=0.55, help="Tight concept merge threshold.")
    parser.add_argument("--wide", type=float, default=0.45, help="Wide concept merge threshold.")
    parser.add_argument("--confidence", type=float, default=0.7, help="Minimum H1 rename confidence for auto-apply.")
    parser.add_argument("--language", default="Chinese", help="Language for H1 rename rationale text.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    if args.mode == "backfill-ledger":
        data = backfill_ledger(args.kb)
    elif args.mode == "rebuild":
        data = rebuild(args.kb, yes=args.yes, strict=args.strict)
    elif args.mode == "compact":
        data = compact(args.kb, fix_h1=args.fix_h1, apply_merges_flag=args.apply_merges)
    elif args.mode == "merge-concepts":
        data = merge_concepts(args.kb, apply=args.apply, tight=args.tight, wide=args.wide)
    else:
        data = h1_rename(args.kb, apply=args.apply, confidence=args.confidence, language=args.language)

    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Maintenance failed."))
        return
    print(f"Mode: {args.mode}")
    if "report" in data:
        print(f"Report: {data['report']}")


if __name__ == "__main__":
    main()
