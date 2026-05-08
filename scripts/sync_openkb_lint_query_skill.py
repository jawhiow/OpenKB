from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


SKILL_NAME = "openkb-lint-query"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_target() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills" / SKILL_NAME
    return Path.home() / ".codex" / "skills" / SKILL_NAME


def validate_source(source: Path) -> None:
    skill_md = source / "SKILL.md"
    if not skill_md.exists():
        raise SystemExit(f"Source skill is missing SKILL.md: {source}")
    text = skill_md.read_text(encoding="utf-8")
    if f"name: {SKILL_NAME}" not in text:
        raise SystemExit(f"Source SKILL.md does not declare name: {SKILL_NAME}")


def validate_target(target: Path) -> None:
    if target.name != SKILL_NAME:
        raise SystemExit(f"Refusing to sync to a target not named {SKILL_NAME}: {target}")


def ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name == "__pycache__" or name.endswith(".pyc") or name == ".DS_Store":
            ignored.add(name)
    return ignored


def sync(source: Path, target: Path, dry_run: bool) -> None:
    validate_source(source)
    validate_target(target)
    if dry_run:
        print(f"Would sync {source} -> {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=ignore)
    print(f"Synced {source} -> {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the repo-managed OpenKB lint/query Codex skill.")
    parser.add_argument(
        "--source",
        default=str(repo_root() / "agent-skills" / SKILL_NAME),
        help="Repo-managed skill source directory.",
    )
    parser.add_argument(
        "--target",
        default=str(default_target()),
        help="Installed Codex skill directory. Defaults to $CODEX_HOME/skills/openkb-lint-query or ~/.codex/skills/openkb-lint-query.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the sync operation without copying.")
    args = parser.parse_args()
    sync(Path(args.source).resolve(), Path(args.target).expanduser().resolve(), args.dry_run)


if __name__ == "__main__":
    main()
