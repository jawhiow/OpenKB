from __future__ import annotations

import argparse
import re
from pathlib import Path

from _common import ensure_md_target, load_json


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _iter_markdown_files(wiki_dir: Path):
    for rel in ("index.md", "log.md"):
        path = wiki_dir / rel
        if path.exists():
            yield path
    for subdir in ("summaries", "concepts", "explorations", "reports"):
        yield from (wiki_dir / subdir).glob("*.md")


def run_structural_lint(kb_dir: Path) -> str:
    wiki_dir = kb_dir / "wiki"
    issues: list[str] = []

    for path in _iter_markdown_files(wiki_dir):
        text = path.read_text(encoding="utf-8")
        for target in WIKILINK_RE.findall(text):
            target_path = ensure_md_target(wiki_dir, target)
            if not target_path.exists():
                issues.append(f"Broken wikilink in {path.relative_to(kb_dir).as_posix()}: [[{target}]]")

    for summary_path in (wiki_dir / "summaries").glob("*.md"):
        source_md = wiki_dir / "sources" / f"{summary_path.stem}.md"
        source_json = wiki_dir / "sources" / f"{summary_path.stem}.json"
        if not source_md.exists() and not source_json.exists():
            issues.append(f"Missing source for summary: {summary_path.name}")

    hashes = load_json(kb_dir / ".openkb" / "hashes.json", {})
    for file_hash, metadata in hashes.items():
        raw_path = metadata.get("raw_path")
        if raw_path and not (kb_dir / raw_path).exists():
            issues.append(f"Missing raw file for hash {file_hash}: {raw_path}")

    if not issues:
        return "# Structural Lint Report\n\nNo structural issues found.\n"

    lines = ["# Structural Lint Report", "", "## Broken Or Missing References"]
    lines.extend(f"- {issue}" for issue in issues)
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run structural lint checks for an agent-native KB.")
    parser.add_argument("kb_dir", nargs="?", default=".", help="Knowledge base root directory")
    args = parser.parse_args()
    print(run_structural_lint(Path(args.kb_dir).resolve()))


if __name__ == "__main__":
    main()
