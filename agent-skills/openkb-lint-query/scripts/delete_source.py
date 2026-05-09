from __future__ import annotations

import argparse
from typing import Any

from _runtime import emit_json, git_status, resolve_kb

try:
    from openkb.source_relations import delete_source_document, get_source_document_detail
    from openkb.log import append_log
    from openkb.kb_git import commit_kb_changes
except Exception:  # pragma: no cover - used when the skill is copied without openkb installed.
    delete_source_document = None  # type: ignore[assignment]
    get_source_document_detail = None  # type: ignore[assignment]
    append_log = None  # type: ignore[assignment]
    commit_kb_changes = None  # type: ignore[assignment]


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


def delete_source(kb: str, selector: str, *, yes: bool = False) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if delete_source_document is None or get_source_document_detail is None:
        return {"ok": False, "error": "The openkb package is not importable in this environment.", "warnings": warnings}

    try:
        document = get_source_document_detail(kb_root, selector)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings}

    would_remove_pages = [
        page["path"]
        for group in ("summaries", "companies", "industries", "concepts")
        for page in document.get("related_pages", {}).get(group, [])
        if not page.get("shared")
    ]
    would_update_pages = [
        page["path"]
        for group in ("companies", "industries", "concepts")
        for page in document.get("related_pages", {}).get(group, [])
        if page.get("shared")
    ]

    if not yes:
        return {
            "ok": True,
            "dry_run": True,
            "kb_root": str(kb_root),
            "document": document,
            "would_remove_pages": would_remove_pages,
            "would_update_pages": would_update_pages,
            "warnings": warnings,
            "git_status_before": git_status(kb_root),
        }

    git_before = git_status(kb_root)
    result = delete_source_document(kb_root, selector)
    document_name = str(result["document"].get("name", selector))
    if append_log is not None:
        append_log(kb_root / "wiki", "delete-source", document_name)
    commit = commit_kb_changes(kb_root, f"Delete source {document_name}") if commit_kb_changes is not None else None
    return {
        "ok": True,
        "dry_run": False,
        "kb_root": str(kb_root),
        "document": result["document"],
        "removed_pages": result["removed_pages"],
        "updated_pages": result["updated_pages"],
        "removed_files": result["removed_files"],
        "commit": _commit_payload(commit),
        "warnings": warnings,
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete an indexed OpenKB source document.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--selector", required=True, help="Source hash, hash prefix, file name, or stem.")
    parser.add_argument("--yes", action="store_true", help="Actually delete instead of previewing the impact.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = delete_source(args.kb, args.selector, yes=args.yes)
    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Delete failed."))
        return
    if data.get("dry_run"):
        print(f"Preview delete: {data['document'].get('name', args.selector)}")
        print(f"Would remove pages: {len(data['would_remove_pages'])}")
        print(f"Would update pages: {len(data['would_update_pages'])}")
        return
    print(f"Deleted: {data['document'].get('name', args.selector)}")
    print(f"Removed pages: {len(data['removed_pages'])}")
    print(f"Updated pages: {len(data['updated_pages'])}")


if __name__ == "__main__":
    main()
