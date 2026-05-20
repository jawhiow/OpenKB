from __future__ import annotations

import subprocess
from pathlib import Path

import openkb.kb_git as kb_git
from openkb.kb_git import commit_kb_changes, commit_kb_paths, ensure_kb_git


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


def test_commit_kb_paths_can_record_ledger_when_legacy_gitignore_ignores_openkb(tmp_path: Path):
    kb_dir = tmp_path / "kb"
    (kb_dir / ".openkb").mkdir(parents=True)
    (kb_dir / "wiki").mkdir()
    (kb_dir / ".gitignore").write_text(".openkb/\n", encoding="utf-8")
    (kb_dir / ".openkb" / "document_ledger.json").write_text(
        '{"version":1,"documents":{}}\n',
        encoding="utf-8",
    )

    result = commit_kb_paths(kb_dir, "Review summaries", [".openkb/document_ledger.json"])

    assert result.committed is True
    tracked = set(_git(kb_dir, "ls-files").splitlines())
    assert ".gitignore" in tracked
    assert ".openkb/document_ledger.json" in tracked
    assert _git(kb_dir, "log", "-1", "--pretty=%s") == "Review summaries"


def test_commit_kb_paths_skips_missing_untracked_pathspecs(tmp_path: Path):
    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()

    result = commit_kb_paths(kb_dir, "Review summaries", [".openkb/document_ledger.json"])

    assert result.git_available is True
    assert result.committed is True
    assert set(_git(kb_dir, "ls-files").splitlines()) == {".gitignore"}
    empty = commit_kb_paths(kb_dir, "Review summaries", [".openkb/document_ledger.json"])
    assert empty.committed is False
    assert empty.skipped_reason == "no_changes"


def test_run_git_decodes_output_as_utf8_with_replacement(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(kb_git.subprocess, "run", fake_run)

    kb_git._run_git(tmp_path, "status")

    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"


def test_run_git_retries_transient_index_lock(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(
                128,
                cmd,
                stderr="fatal: Unable to create '.git/index.lock': File exists.",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(kb_git.subprocess, "run", fake_run)
    monkeypatch.setattr(kb_git, "_GIT_LOCK_RETRY_DELAY_SECONDS", 0)

    result = kb_git._run_git(tmp_path, "add", "--", ".gitignore")

    assert result.stdout == "ok\n"
    assert len(calls) == 2
