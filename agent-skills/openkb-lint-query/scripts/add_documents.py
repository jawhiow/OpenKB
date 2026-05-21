from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _runtime import bootstrap_openkb_repo_path, emit_json, git_status, resolve_kb

bootstrap_openkb_repo_path()

try:
    from openkb.cli import SUPPORTED_EXTENSIONS, _setup_llm_key, add_single_file
    from openkb.config import DEFAULT_CONFIG, load_config
    from openkb.ingest_gate import (
        evaluate_candidate,
        gate_is_active,
        gate_summary_line,
        ingest_gate_config,
        record_gate_decision,
        should_continue_ingest,
    )
    from openkb.kb_git import commit_kb_changes
    from openkb.log import append_log
    from openkb.workflows.auto_review import run_auto_review
    from openkb.workflows.import_pipeline import import_document_source
    from openkb.workflows.promotion_pipeline import promote_summary_documents
    from openkb.workflows.summary_pipeline import summarize_documents
except Exception:  # pragma: no cover - used when the skill is copied without openkb installed.
    SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
    DEFAULT_CONFIG = {"language": "en"}  # type: ignore[assignment]
    _setup_llm_key = None  # type: ignore[assignment]
    add_single_file = None  # type: ignore[assignment]
    evaluate_candidate = None  # type: ignore[assignment]
    gate_is_active = None  # type: ignore[assignment]
    gate_summary_line = None  # type: ignore[assignment]
    ingest_gate_config = None  # type: ignore[assignment]
    load_config = None  # type: ignore[assignment]
    record_gate_decision = None  # type: ignore[assignment]
    should_continue_ingest = None  # type: ignore[assignment]
    commit_kb_changes = None  # type: ignore[assignment]
    append_log = None  # type: ignore[assignment]
    run_auto_review = None  # type: ignore[assignment]
    import_document_source = None  # type: ignore[assignment]
    promote_summary_documents = None  # type: ignore[assignment]
    summarize_documents = None  # type: ignore[assignment]


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


def _empty_summary_result() -> dict[str, Any]:
    return {"generated": 0, "skipped": 0, "failed": 0, "total": 0, "failures": [], "documents": []}


def _empty_auto_review_result() -> dict[str, Any]:
    return {
        "run_id": "",
        "dry_run": False,
        "total": 0,
        "approved": 0,
        "rejected": 0,
        "held_for_human": 0,
        "errors": 0,
        "decisions": [],
    }


def _empty_promotion_result() -> dict[str, Any]:
    return {"promoted": 0, "skipped": 0, "failed": 0, "total": 0, "failures": [], "documents": []}


def _commit_if_changed(kb_root: Path, message: str) -> dict[str, Any]:
    if commit_kb_changes is None:
        return _commit_payload(None)
    return _commit_payload(commit_kb_changes(kb_root, message))


def _setup_workflow_runtime(kb_root: Path) -> None:
    if _setup_llm_key is not None:
        _setup_llm_key(kb_root)


def _run_ingest_gate(
    kb_root: Path,
    file_path: Path,
    *,
    force_gate_pass: bool,
    force_gate_reject: bool,
    gate_reason: str,
    gate_operator: str,
) -> dict[str, Any] | None:
    if (
        load_config is None
        or ingest_gate_config is None
        or gate_is_active is None
        or evaluate_candidate is None
        or should_continue_ingest is None
    ):
        return None

    config = load_config(kb_root / ".openkb" / "config.yaml")
    gate_config = ingest_gate_config(config)
    if not gate_is_active(gate_config, force_pass=force_gate_pass, force_reject=force_gate_reject):
        return None
    if force_gate_pass and not getattr(gate_config, "allow_force_pass", False):
        raise RuntimeError("Ingest gate force-pass is disabled by config.")
    if force_gate_reject and not getattr(gate_config, "allow_force_reject", False):
        raise RuntimeError("Ingest gate force-reject is disabled by config.")

    _setup_workflow_runtime(kb_root)
    gate_model = str(config.get("ingest_gate", {}).get("model") or config.get("model") or "").strip()
    if not gate_model:
        gate_model = str(DEFAULT_CONFIG.get("model") or "gpt-5.4-mini")
    result = evaluate_candidate(
        file_path,
        kb_root,
        model=gate_model,
        language=str(config.get("language") or DEFAULT_CONFIG.get("language") or "en"),
        config=gate_config,
        force_pass=force_gate_pass,
        force_reject=force_gate_reject,
        force_reason=gate_reason,
        operator=gate_operator,
    )
    if bool(getattr(gate_config, "log_all_decisions", False)):
        if record_gate_decision is not None:
            record_gate_decision(
                kb_root,
                result,
                language=str(config.get("language") or DEFAULT_CONFIG.get("language") or "en"),
            )
        if append_log is not None and gate_summary_line is not None:
            append_log(kb_root / "wiki", "ingest-gate", f"{file_path.name} | {gate_summary_line(result)}")
        score = result.get("total_score", "unscored")
        _commit_if_changed(kb_root, f"Gate {file_path.name} -> {result.get('final_decision')} {score}")
    return result


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


def _staged_add_documents(
    kb_root: Path,
    files: list[Path],
    *,
    force: bool,
    strict: bool,
    strategy_override: str | None,
    force_gate_pass: bool,
    force_gate_reject: bool,
    gate_reason: str,
    gate_operator: str,
    summarize: bool,
    auto_review: bool,
    promote: bool,
    summary_model: str | None,
    promotion_model: str | None,
) -> dict[str, Any]:
    if import_document_source is None:
        return {"ok": False, "error": "The OpenKB import pipeline is not importable in this environment."}
    if summarize and summarize_documents is None:
        return {"ok": False, "error": "The OpenKB summary pipeline is not importable in this environment."}
    if auto_review and run_auto_review is None:
        return {"ok": False, "error": "The OpenKB auto-review pipeline is not importable in this environment."}
    if promote and promote_summary_documents is None:
        return {"ok": False, "error": "The OpenKB promotion pipeline is not importable in this environment."}

    imported: list[dict[str, Any]] = []
    skipped: list[str] = []
    gate_blocked: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    file_hashes: list[str] = []
    commits: list[dict[str, Any]] = []

    for file_path in files:
        try:
            gate_result = _run_ingest_gate(
                kb_root,
                file_path,
                force_gate_pass=force_gate_pass,
                force_gate_reject=force_gate_reject,
                gate_reason=gate_reason,
                gate_operator=gate_operator,
            )
            if gate_result is not None and should_continue_ingest is not None and not should_continue_ingest(gate_result):
                skipped.append(str(file_path))
                gate_blocked.append({
                    "path": str(file_path),
                    "decision": gate_result.get("final_decision"),
                    "total_score": gate_result.get("total_score"),
                    "summary": gate_summary_line(gate_result) if gate_summary_line is not None else "",
                })
                continue

            result = import_document_source(
                file_path,
                kb_root,
                force=force,
                strategy_override=strategy_override,
            )
            if result.get("skipped"):
                skipped.append(str(file_path))
                file_hash = str(result.get("file_hash") or "").strip()
                if file_hash:
                    file_hashes.append(file_hash)
                continue
            imported.append(result)
            file_hash = str(result.get("file_hash") or "").strip()
            if file_hash:
                file_hashes.append(file_hash)
        except Exception as exc:
            failed.append({"path": str(file_path), "error": str(exc)})
            if strict:
                break

    if imported:
        if append_log is not None:
            append_log(kb_root / "wiki", "import", f"{len(imported)} source document(s)")
        commits.append(_commit_if_changed(kb_root, f"Import {len(imported)} source document(s)"))

    summary_result = _empty_summary_result()
    if summarize and file_hashes:
        try:
            _setup_workflow_runtime(kb_root)
            kwargs: dict[str, Any] = {"file_hashes": file_hashes, "force": force, "max_workers": 1}
            if summary_model:
                kwargs["model"] = summary_model
            summary_result = summarize_documents(kb_root, **kwargs)
            if summary_result.get("generated"):
                commits.append(_commit_if_changed(kb_root, f"Summarize {summary_result['generated']} document(s)"))
        except Exception as exc:
            summary_result = _empty_summary_result()
            summary_result["failed"] = len(file_hashes)
            summary_result["total"] = len(file_hashes)
            summary_result["failures"] = [{"file_hash": file_hash, "error": str(exc)} for file_hash in file_hashes]
            if strict:
                failed.append({"path": str(kb_root), "error": f"Summary generation failed: {exc}"})

    auto_review_result = _empty_auto_review_result()
    if auto_review and file_hashes and int(summary_result.get("failed") or 0) == 0:
        try:
            _setup_workflow_runtime(kb_root)
            auto_review_result = run_auto_review(
                kb_root,
                file_hashes=file_hashes,
                dry_run=False,
                operator=gate_operator,
            )
            if auto_review_result.get("total"):
                commits.append(_commit_if_changed(kb_root, f"Auto-review {auto_review_result['total']} summary document(s)"))
        except Exception as exc:
            auto_review_result = _empty_auto_review_result()
            auto_review_result["errors"] = len(file_hashes)
            auto_review_result["decisions"] = [{"file_hash": file_hash, "error": str(exc)} for file_hash in file_hashes]
            if strict:
                failed.append({"path": str(kb_root), "error": f"Auto-review failed: {exc}"})

    promotion_result = _empty_promotion_result()
    if promote and file_hashes and int(summary_result.get("failed") or 0) == 0 and int(auto_review_result.get("errors") or 0) == 0:
        try:
            _setup_workflow_runtime(kb_root)
            kwargs = {"file_hashes": file_hashes, "force": False, "max_workers": 1}
            if promotion_model:
                kwargs["model"] = promotion_model
            promotion_result = promote_summary_documents(kb_root, **kwargs)
            if promotion_result.get("promoted"):
                commits.append(_commit_if_changed(kb_root, f"Promote {promotion_result['promoted']} summary document(s)"))
        except Exception as exc:
            promotion_result = _empty_promotion_result()
            promotion_result["failed"] = len(file_hashes)
            promotion_result["total"] = len(file_hashes)
            promotion_result["failures"] = [{"file_hash": file_hash, "error": str(exc)} for file_hash in file_hashes]
            if strict:
                failed.append({"path": str(kb_root), "error": f"Promotion failed: {exc}"})

    ok = not failed and int(summary_result.get("failed") or 0) == 0 and int(auto_review_result.get("errors") or 0) == 0 and int(promotion_result.get("failed") or 0) == 0
    return {
        "ok": ok,
        "workflow": "staged",
        "import_only": False,
        "summarize": summarize,
        "auto_review_enabled": auto_review,
        "promote_enabled": promote,
        "imported": imported,
        "file_hashes": file_hashes,
        "added": [],
        "skipped": skipped,
        "gate_blocked": gate_blocked,
        "failed": failed,
        "summary": summary_result,
        "auto_review": auto_review_result,
        "promotion": promotion_result,
        "commits": commits,
    }


def _legacy_compile_documents(
    kb_root: Path,
    files: list[Path],
    *,
    force: bool,
    strict: bool,
    force_gate_pass: bool,
    force_gate_reject: bool,
    gate_reason: str,
    gate_operator: str,
) -> dict[str, Any]:
    if add_single_file is None:
        return {"ok": False, "error": "The OpenKB add helper is not importable in this environment."}

    added: list[str] = []
    skipped: list[str] = []
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
                skipped.append(str(file_path))
        except Exception as exc:
            failed.append({"path": str(file_path), "error": str(exc)})
            if strict:
                break

    return {
        "ok": not failed,
        "workflow": "legacy_compile",
        "import_only": False,
        "added": added,
        "skipped": skipped,
        "failed": failed,
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
    summarize: bool = True,
    auto_review: bool = False,
    promote: bool = False,
    summary_model: str | None = None,
    promotion_model: str | None = None,
    legacy_compile: bool = False,
) -> dict[str, Any]:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings, "added": [], "skipped": []}
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

    if legacy_compile:
        result = _legacy_compile_documents(
            kb_root,
            files,
            force=force,
            strict=strict,
            force_gate_pass=force_gate_pass,
            force_gate_reject=force_gate_reject,
            gate_reason=gate_reason,
            gate_operator=gate_operator,
        )
    else:
        result = _staged_add_documents(
            kb_root,
            files,
            force=force,
            strict=strict,
            strategy_override=strategy_override,
            force_gate_pass=force_gate_pass,
            force_gate_reject=force_gate_reject,
            gate_reason=gate_reason,
            gate_operator=gate_operator,
            summarize=summarize,
            auto_review=auto_review,
            promote=promote,
            summary_model=summary_model,
            promotion_model=promotion_model,
        )

    result.update(
        {
            "kb_root": str(kb_root),
            "path": str(target),
            "force": force,
            "strict": strict,
            "strategy_override": strategy_override,
            "force_gate_pass": force_gate_pass,
            "force_gate_reject": force_gate_reject,
            "skipped_unsupported": [str(item) for item in skipped],
            "git_status_before": git_before,
            "git_status_after": git_status(kb_root),
            "warnings": warnings,
        }
    )
    if legacy_compile:
        result["skipped"] = [*result.get("skipped", []), *[str(item) for item in skipped]]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Add supported documents to an OpenKB knowledge base.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--path", required=True, help="File or directory to add.")
    parser.add_argument("--force", action="store_true", help="Re-import/re-score even if the document hash is already indexed.")
    parser.add_argument("--strict", action="store_true", help="Stop on the first conversion or compilation failure.")
    parser.add_argument("--import-only", action="store_true", help="Import raw/source artifacts without compiling summaries or wiki pages.")
    parser.add_argument("--strategy-override", default=None, help="Override PDF preparation strategy, for example ocr-pageindex-local.")
    parser.add_argument("--no-summarize", action="store_true", help="After staged import, do not generate scored review summaries.")
    parser.add_argument("--auto-review", action="store_true", help="After summary scoring, apply OpenKB auto-review decisions.")
    parser.add_argument("--promote", action="store_true", help="Promote approved summaries into downstream wiki pages.")
    parser.add_argument("--summary-model", default=None, help="Override the model used for staged summary scoring.")
    parser.add_argument("--promotion-model", default=None, help="Override the model used for staged promotion.")
    parser.add_argument("--legacy-compile", action="store_true", help="Use the legacy one-step add path that compiles and promotes immediately.")
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
        summarize=not args.no_summarize,
        auto_review=args.auto_review,
        promote=args.promote,
        summary_model=args.summary_model,
        promotion_model=args.promotion_model,
        legacy_compile=args.legacy_compile,
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
    elif data.get("workflow") == "staged":
        print(f"Imported: {len(data.get('imported', []))}")
        print(f"Summaries generated: {data.get('summary', {}).get('generated', 0)}")
        print(f"Promoted: {data.get('promotion', {}).get('promoted', 0)}")
        print(f"Skipped unsupported/no-op: {len(data.get('skipped', [])) + len(data.get('skipped_unsupported', []))}")
    else:
        print(f"Added: {len(data['added'])}")
        print(f"Skipped unsupported/no-op: {len(data['skipped'])}")


if __name__ == "__main__":
    main()
