from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _runtime import (
    append_log,
    draft_page,
    emit_json,
    ensure_index_entry,
    read_text,
    resolve_kb,
    safe_markdown_path,
    wiki_root,
    write_text,
)


def load_plan(plan_path: str) -> dict[str, Any]:
    path = Path(plan_path).resolve()
    return json.loads(read_text(path))


def should_apply(item: dict[str, Any]) -> bool:
    return bool(item.get("approved") or item.get("safe", True))


def apply_plan(kb: str, plan: str) -> dict:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    wiki = wiki_root(kb_root)
    data = load_plan(plan)
    items = data.get("fix_plan") or data.get("fixes") or []
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    changed: list[str] = []

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        if not should_apply(raw_item):
            skipped.append(raw_item)
            continue
        action = str(raw_item.get("action") or "")
        path = str(raw_item.get("path") or "")
        title = str(raw_item.get("title") or Path(path).stem)
        reason = str(raw_item.get("reason") or "Approved lint fix.")

        if action == "create_draft_page":
            resolved = safe_markdown_path(wiki, path)
            if resolved is None:
                skipped.append({**raw_item, "skip_reason": "unsafe path"})
                continue
            full, rel = resolved
            if full.exists():
                skipped.append({**raw_item, "skip_reason": "already exists"})
                continue
            page_type = str(raw_item.get("page_type") or rel.split("/", 1)[0])
            write_text(full, draft_page(title, page_type, reason))
            ensure_index_entry(wiki, rel, title, "lint draft")
            applied.append(raw_item)
            changed.extend([rel, "index.md"])
        elif action == "ensure_index_entry":
            if ensure_index_entry(wiki, path, title, "lint indexed"):
                applied.append(raw_item)
                changed.append("index.md")
            else:
                skipped.append({**raw_item, "skip_reason": "index already contains link"})
        elif action == "append_source_evidence_todo":
            skipped.append({**raw_item, "skip_reason": "deprecated action"})
        else:
            skipped.append({**raw_item, "skip_reason": f"manual action or unsupported action: {action}"})

    if applied:
        append_log(wiki, "lint", f"applied approved fixes from {Path(plan).name}: {len(applied)}")
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "wiki_root": str(wiki),
        "applied": applied,
        "skipped": skipped,
        "changed_files": sorted(set(changed)),
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply approved OpenKB lint fix-plan items.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--plan", required=True, help="JSON lint report or fix plan.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()
    data = apply_plan(args.kb, args.plan)
    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Apply fixes failed."))
        return
    print(f"Applied: {len(data['applied'])}")
    print(f"Skipped: {len(data['skipped'])}")


if __name__ == "__main__":
    main()
