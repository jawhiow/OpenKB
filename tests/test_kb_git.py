from __future__ import annotations

import subprocess
from pathlib import Path

from openkb.kb_git import commit_kb_changes, ensure_kb_git


def _git(kb_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=kb_dir,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def test_ensure_kb_git_initializes_repo_and_ignores_raw(tmp_path: Path):
    kb_dir = tmp_path / "kb"
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki").mkdir()
    (kb_dir / ".openkb").mkdir()
    (kb_dir / "raw" / "private.pdf").write_bytes(b"%PDF")
    (kb_dir / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
    (kb_dir / ".openkb" / "hashes.json").write_text("{}", encoding="utf-8")

    result = ensure_kb_git(kb_dir, initial_commit_message="Initialize KB")

    assert result.git_available is True
    assert result.committed is True
    assert (kb_dir / ".git").is_dir()
    assert "/raw/" in (kb_dir / ".gitignore").read_text(encoding="utf-8")
    tracked = set(_git(kb_dir, "ls-files").splitlines())
    assert ".gitignore" in tracked
    assert "wiki/index.md" in tracked
    assert ".openkb/hashes.json" in tracked
    assert "raw/private.pdf" not in tracked
    assert _git(kb_dir, "log", "-1", "--pretty=%s") == "Initialize KB"


def test_commit_kb_changes_skips_empty_commits_and_keeps_raw_untracked(tmp_path: Path):
    kb_dir = tmp_path / "kb"
    (kb_dir / "raw").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / ".openkb").mkdir()
    (kb_dir / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")

    ensure_kb_git(kb_dir, initial_commit_message="Initial")
    before_count = _git(kb_dir, "rev-list", "--count", "HEAD")

    (kb_dir / "wiki" / "concepts" / "Git.md").write_text("# Git\n", encoding="utf-8")
    (kb_dir / "raw" / "source.pdf").write_bytes(b"%PDF")
    changed = commit_kb_changes(kb_dir, "Add Git concept")
    after_count = _git(kb_dir, "rev-list", "--count", "HEAD")

    empty = commit_kb_changes(kb_dir, "No-op")
    final_count = _git(kb_dir, "rev-list", "--count", "HEAD")

    tracked = set(_git(kb_dir, "ls-files").splitlines())
    assert changed.committed is True
    assert empty.committed is False
    assert int(after_count) == int(before_count) + 1
    assert final_count == after_count
    assert "wiki/concepts/Git.md" in tracked
    assert "raw/source.pdf" not in tracked
