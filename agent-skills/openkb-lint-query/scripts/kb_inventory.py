from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _runtime import bootstrap_openkb_repo_path, emit_json, markdown_pages, read_text, resolve_kb, title_from_text

bootstrap_openkb_repo_path()

try:
    from openkb.document_ledger import list_effective_document_ledger_records
    from openkb.source_relations import get_source_document_detail, get_source_documents
except Exception:  # pragma: no cover - used when copied without openkb installed.
    list_effective_document_ledger_records = None  # type: ignore[assignment]
    get_source_document_detail = None  # type: ignore[assignment]
    get_source_documents = None  # type: ignore[assignment]


CONTENT_DIRS = ("sources", "summaries", "companies", "industries", "concepts", "explorations", "reports")


def _hashes(kb_root: Path) -> dict[str, Any]:
    path = kb_root / ".openkb" / "hashes.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _page_list(kb_root: Path) -> dict[str, list[dict[str, str]]]:
    wiki = kb_root / "wiki"
    pages: dict[str, list[dict[str, str]]] = {name: [] for name in CONTENT_DIRS if name != "sources"}
    for path in markdown_pages(wiki, content_only=False):
        rel = path.relative_to(wiki).as_posix()
        first = rel.split("/", 1)[0]
        if first not in pages:
            continue
        text = read_text(path)
        pages[first].append({"path": rel, "title": title_from_text(text, path.stem)})
    return pages


def _latest_mtime(paths: list[Path]) -> str:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return ""
    import datetime

    newest = max(existing, key=lambda path: path.stat().st_mtime)
    return datetime.datetime.fromtimestamp(newest.stat().st_mtime).isoformat(timespec="seconds")


def status(kb: str) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    wiki = kb_root / "wiki"
    counts: dict[str, int] = {}
    for name in CONTENT_DIRS:
        path = wiki / name
        counts[name] = len(list(path.glob("*.md"))) if path.exists() else 0
    raw_dir = kb_root / "raw"
    counts["raw"] = len([item for item in raw_dir.iterdir() if item.is_file()]) if raw_dir.exists() else 0
    hashes = _hashes(kb_root)
    ledger_records = (
        list_effective_document_ledger_records(kb_root)
        if list_effective_document_ledger_records is not None
        else {}
    )
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "counts": counts,
        "indexed_documents": len(hashes),
        "ledger_records": len(ledger_records),
        "last_compile": _latest_mtime(list((wiki / "summaries").glob("*.md")) if (wiki / "summaries").exists() else []),
        "last_lint": _latest_mtime(list((wiki / "reports").glob("*.md")) if (wiki / "reports").exists() else []),
        "warnings": warnings,
    }


def inventory(kb: str, *, include_pages: bool = False, include_ledger: bool = False) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if get_source_documents is not None:
        documents = get_source_documents(kb_root)
    else:
        documents = [dict({"hash": key}, **value) for key, value in _hashes(kb_root).items() if isinstance(value, dict)]
    data: dict[str, Any] = {
        "ok": True,
        "kb_root": str(kb_root),
        "documents": documents,
        "warnings": warnings,
    }
    if include_pages:
        data["pages"] = _page_list(kb_root)
    if include_ledger:
        if list_effective_document_ledger_records is None:
            data["ledger"] = {}
            data.setdefault("warnings", []).append("Document ledger helper is unavailable.")
        else:
            data["ledger"] = list_effective_document_ledger_records(kb_root)
    return data


def source_detail(kb: str, selector: str) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    if get_source_document_detail is None:
        return {"ok": False, "error": "The openkb source helpers are not importable in this environment.", "warnings": warnings}
    try:
        document = get_source_document_detail(kb_root, selector)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings}
    return {"ok": True, "kb_root": str(kb_root), "document": document, "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect an OpenKB runtime knowledge base inventory.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--mode", choices=("status", "list", "source"), default="status")
    parser.add_argument("--selector", default="", help="Source hash, hash prefix, file name, or stem for --mode source.")
    parser.add_argument("--include-pages", action="store_true", help="Include generated wiki page lists in list mode.")
    parser.add_argument("--include-ledger", action="store_true", help="Include effective document ledger records in list mode.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    if args.mode == "status":
        data = status(args.kb)
    elif args.mode == "source":
        if not args.selector:
            data = {"ok": False, "error": "--selector is required for source mode."}
        else:
            data = source_detail(args.kb, args.selector)
    else:
        data = inventory(args.kb, include_pages=args.include_pages, include_ledger=args.include_ledger)

    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Inventory failed."))
        return
    if args.mode == "status":
        print(f"Indexed documents: {data['indexed_documents']}")
        for name, count in data["counts"].items():
            print(f"{name}: {count}")
    elif args.mode == "source":
        document = data["document"]
        print(f"Source document: {document.get('name', args.selector)}")
        print(f"Related pages: {document.get('related_count', 0)}")
    else:
        print(f"Documents: {len(data['documents'])}")


if __name__ == "__main__":
    main()
