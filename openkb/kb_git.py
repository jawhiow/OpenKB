"""Git integration helpers for runtime OpenKB knowledge bases."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


KB_GITIGNORE_LINES = (
    "/raw/",
    "/.env",
    "/.openkb/ocr/cache/",
    "/.openkb/llm-usage/",
    "/.openkb/pageindex-local/",
    "/.openkb/model-pool/status.json",
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
)

_UNCACHED_PATHS = (
    "raw",
    ".env",
    ".openkb/ocr/cache",
    ".openkb/llm-usage",
    ".openkb/pageindex-local",
    ".openkb/model-pool/status.json",
)


@dataclass(frozen=True)
class KbGitResult:
    """Outcome of a KB Git operation."""

    git_available: bool
    committed: bool = False
    commit_hash: str = ""
    message: str = ""
    skipped_reason: str = ""


def _run_git(kb_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=kb_dir,
        check=check,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def _git_available(kb_dir: Path) -> bool:
    try:
        _run_git(kb_dir, "--version")
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def _has_own_git_dir(kb_dir: Path) -> bool:
    return (kb_dir / ".git").exists()


def _ensure_gitignore(kb_dir: Path) -> None:
    gitignore = kb_dir / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    seen = {line.strip() for line in existing}
    missing = [line for line in KB_GITIGNORE_LINES if line not in seen]
    if not missing:
        return
    lines = list(existing)
    if lines and lines[-1].strip():
        lines.append("")
    if not lines:
        lines.append("# OpenKB runtime ignores")
    elif "# OpenKB runtime ignores" not in seen:
        lines.append("# OpenKB runtime ignores")
    lines.extend(missing)
    gitignore.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def _ensure_commit_identity(kb_dir: Path) -> None:
    name = _run_git(kb_dir, "config", "--get", "user.name", check=False).stdout.strip()
    if not name:
        _run_git(kb_dir, "config", "user.name", "OpenKB")
    email = _run_git(kb_dir, "config", "--get", "user.email", check=False).stdout.strip()
    if not email:
        _run_git(kb_dir, "config", "user.email", "openkb@local")


def _untrack_protected_paths(kb_dir: Path) -> None:
    for pathspec in _UNCACHED_PATHS:
        tracked = _run_git(kb_dir, "ls-files", "--", pathspec, check=False).stdout.strip()
        if tracked:
            _run_git(kb_dir, "rm", "-r", "--cached", "--quiet", "--", pathspec, check=False)


def _stage_and_commit(kb_dir: Path, message: str) -> KbGitResult:
    _ensure_gitignore(kb_dir)
    _untrack_protected_paths(kb_dir)
    _run_git(kb_dir, "add", "-A", "--", ".")
    has_changes = _run_git(kb_dir, "diff", "--cached", "--quiet", check=False).returncode != 0
    if not has_changes:
        return KbGitResult(git_available=True, committed=False, skipped_reason="no_changes")

    _ensure_commit_identity(kb_dir)
    clean_message = " ".join((message or "Update knowledge base").split())
    _run_git(kb_dir, "commit", "-m", clean_message)
    commit_hash = _run_git(kb_dir, "rev-parse", "--short", "HEAD").stdout.strip()
    return KbGitResult(
        git_available=True,
        committed=True,
        commit_hash=commit_hash,
        message=clean_message,
    )


def ensure_kb_git(kb_dir: Path, *, initial_commit_message: str | None = None) -> KbGitResult:
    """Ensure *kb_dir* is its own Git repository with OpenKB runtime ignores."""
    kb_dir = Path(kb_dir).resolve()
    kb_dir.mkdir(parents=True, exist_ok=True)
    if not _git_available(kb_dir):
        return KbGitResult(git_available=False, skipped_reason="git_unavailable")

    if not _has_own_git_dir(kb_dir):
        _run_git(kb_dir, "init")
    _ensure_gitignore(kb_dir)
    _untrack_protected_paths(kb_dir)

    if initial_commit_message:
        return _stage_and_commit(kb_dir, initial_commit_message)
    return KbGitResult(git_available=True, committed=False)


def commit_kb_changes(kb_dir: Path, message: str) -> KbGitResult:
    """Commit current KB changes when there is anything stageable to record."""
    kb_dir = Path(kb_dir).resolve()
    if not _git_available(kb_dir):
        return KbGitResult(git_available=False, skipped_reason="git_unavailable")
    ensure_kb_git(kb_dir)
    return _stage_and_commit(kb_dir, message)
