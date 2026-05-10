from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _runtime import emit_json, git_status, resolve_kb

try:
    from openkb.cli import SUPPORTED_EXTENSIONS, add_single_file
except Exception:  # pragma: no cover - used when the skill is copied without openkb installed.
    SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
    add_single_file = None  # type: ignore[assignment]


def _supported_files(target: Path) -> tuple[list[Path], list[Path]]:
    if target.is_file():
        if target.suffix.lower() in SUPPORTED_EXTENSIONS:
            return [target], []
        return [], [target]

    files: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
        else:
            skipped.append(path)
    return files, skipped


def _hash_registry_names(kb_root: Path) -> set[str]:
    path = kb_root / ".openkb" / "hashes.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(meta.get("name", "")) for meta in data.values() if isinstance(meta, dict)}


def add_documents(kb: str, path: str, *, force: bool = False, strict: bool = False) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings, "added": [], "skipped": []}
    if add_single_file is None:
        return {"ok": False, "error": "The openkb package is not importable in this environment.", "warnings": warnings, "added": [], "skipped": []}

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return {"ok": False, "error": f"Path does not exist: {path}", "warnings": warnings, "added": [], "skipped": []}

    files, skipped = _supported_files(target)
    if not files:
        if target.is_file():
            return {
                "ok": False,
                "error": f"Unsupported file type: {target.suffix}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
                "warnings": warnings,
                "added": [],
                "skipped": [str(item) for item in skipped],
            }
        return {"ok": False, "error": f"No supported files found in {path}.", "warnings": warnings, "added": [], "skipped": [str(item) for item in skipped]}

    git_before = git_status(kb_root)
    added: list[str] = []
    failed: list[dict[str, str]] = []
    for file_path in files:
        try:
            before_names = _hash_registry_names(kb_root)
            add_single_file(file_path, kb_root, force=force, strict=strict)
            after_names = _hash_registry_names(kb_root)
            if file_path.name in after_names and (force or file_path.name not in before_names):
                added.append(str(file_path))
            else:
                skipped.append(file_path)
        except Exception as exc:
            failed.append({"path": str(file_path), "error": str(exc)})
            if strict:
                break

    return {
        "ok": not failed,
        "kb_root": str(kb_root),
        "path": str(target),
        "force": force,
        "strict": strict,
        "added": added,
        "skipped": [str(item) for item in skipped],
        "failed": failed,
        "git_status_before": git_before,
        "git_status_after": git_status(kb_root),
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Add supported documents to an OpenKB knowledge base.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--path", required=True, help="File or directory to add.")
    parser.add_argument("--force", action="store_true", help="Recompile even if the document hash is already indexed.")
    parser.add_argument("--strict", action="store_true", help="Stop on the first conversion or compilation failure.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = add_documents(args.kb, args.path, force=args.force, strict=args.strict)
    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Add failed."))
        for item in data.get("failed", []):
            print(f"- {item['path']}: {item['error']}")
        return
    print(f"Added: {len(data['added'])}")
    print(f"Skipped unsupported: {len(data['skipped'])}")


if __name__ == "__main__":
    main()
