from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _runtime import bootstrap_openkb_repo_path, emit_json, git_status, resolve_kb

bootstrap_openkb_repo_path()

try:
    from openkb.cli import SUPPORTED_EXTENSIONS, add_single_file
    from openkb.kb_git import commit_kb_changes
    from openkb.log import append_log
    from openkb.workflows.import_pipeline import import_document_source
except Exception:  # pragma: no cover - used when the skill is copied without openkb installed.
    SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
    add_single_file = None  # type: ignore[assignment]
    commit_kb_changes = None  # type: ignore[assignment]
    append_log = None  # type: ignore[assignment]
    import_document_source = None  # type: ignore[assignment]


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


def _import_only_documents(
    kb_root: Path,
    files: list[Path],
    *,
    force: bool,
    strict: bool,
    strategy_override: str | None,
) -> dict[str, Any]:
    if import_document_source is None:
        return {"ok": False, "error": "The OpenKB import pipeline is not importable in this environment."}

    imported: list[dict[str, Any]] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    for file_path in files:
        try:
            result = import_document_source(
                file_path,
                kb_root,
                force=force,
                strategy_override=strategy_override,
            )
            if result.get("skipped"):
                skipped.append(str(file_path))
            else:
                imported.append(result)
        except Exception as exc:
            failed.append({"path": str(file_path), "error": str(exc)})
            if strict:
                break

    commit = None
    if imported:
        if append_log is not None:
            append_log(kb_root / "wiki", "import", f"{len(imported)} source document(s)")
        if commit_kb_changes is not None:
            commit = commit_kb_changes(kb_root, f"Import {len(imported)} source document(s)")

    return {
        "ok": not failed,
        "import_only": True,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "commit": _commit_payload(commit),
    }


def add_documents(
    kb: str,
    path: str,
    *,
    force: bool = False,
    strict: bool = False,
    import_only: bool = False,
    strategy_override: str | None = None,
    force_gate_pass: bool = False,
    force_gate_reject: bool = False,
    gate_reason: str = "",
    gate_operator: str = "",
) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings, "added": [], "skipped": []}
    if add_single_file is None and not import_only:
        return {"ok": False, "error": "The openkb package is not importable in this environment.", "warnings": warnings, "added": [], "skipped": []}
    if force_gate_pass and force_gate_reject:
        return {"ok": False, "error": "Cannot use force_gate_pass and force_gate_reject together.", "warnings": warnings, "added": [], "skipped": []}
    if (force_gate_pass or force_gate_reject) and not gate_reason.strip():
        return {"ok": False, "error": "gate_reason is required when forcing an ingest gate decision.", "warnings": warnings, "added": [], "skipped": []}

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
    if import_only:
        imported = _import_only_documents(
            kb_root,
            files,
            force=force,
            strict=strict,
            strategy_override=strategy_override,
        )
        imported.update(
            {
                "kb_root": str(kb_root),
                "path": str(target),
                "force": force,
                "strict": strict,
                "strategy_override": strategy_override,
                "skipped_unsupported": [str(item) for item in skipped],
                "git_status_before": git_before,
                "git_status_after": git_status(kb_root),
                "warnings": warnings,
            }
        )
        return imported

    added: list[str] = []
    failed: list[dict[str, str]] = []
    for file_path in files:
        try:
            before_names = _hash_registry_names(kb_root)
            kwargs: dict[str, Any] = {"force": force, "strict": strict}
            if force_gate_pass or force_gate_reject:
                kwargs.update(
                    {
                        "force_gate_pass": force_gate_pass,
                        "force_gate_reject": force_gate_reject,
                        "gate_reason": gate_reason,
                        "gate_operator": gate_operator,
                    }
                )
            add_single_file(file_path, kb_root, **kwargs)
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
        "import_only": False,
        "force_gate_pass": force_gate_pass,
        "force_gate_reject": force_gate_reject,
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
    parser.add_argument("--import-only", action="store_true", help="Import raw/source artifacts without compiling summaries or wiki pages.")
    parser.add_argument("--strategy-override", default=None, help="Override PDF preparation strategy, for example ocr-pageindex-local.")
    parser.add_argument("--force-pass", action="store_true", help="Force the ingest gate to allow this document.")
    parser.add_argument("--force-reject", action="store_true", help="Force the ingest gate to reject this document.")
    parser.add_argument("--gate-reason", default="", help="Reason recorded for force-pass or force-reject.")
    parser.add_argument("--gate-operator", default="", help="Operator recorded for a forced gate decision.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = add_documents(
        args.kb,
        args.path,
        force=args.force,
        strict=args.strict,
        import_only=args.import_only,
        strategy_override=args.strategy_override,
        force_gate_pass=args.force_pass,
        force_gate_reject=args.force_reject,
        gate_reason=args.gate_reason,
        gate_operator=args.gate_operator,
    )
    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Add failed."))
        for item in data.get("failed", []):
            print(f"- {item['path']}: {item['error']}")
        return
    if data.get("import_only"):
        print(f"Imported: {len(data.get('imported', []))}")
        print(f"Skipped already imported: {len(data.get('skipped', []))}")
        print(f"Skipped unsupported: {len(data.get('skipped_unsupported', []))}")
    else:
        print(f"Added: {len(data['added'])}")
        print(f"Skipped unsupported/no-op: {len(data['skipped'])}")


if __name__ == "__main__":
    main()
