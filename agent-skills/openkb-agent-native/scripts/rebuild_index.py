from __future__ import annotations

import argparse
from pathlib import Path

from _common import summarize_markdown


def _document_lines(kb_dir: Path) -> list[str]:
    lines: list[str] = []
    for path in sorted((kb_dir / "wiki" / "summaries").glob("*.md")):
        desc = summarize_markdown(path)
        lines.append(f"- [[summaries/{path.stem}]] (short) - {desc}".rstrip())
    return lines


def _concept_lines(kb_dir: Path) -> list[str]:
    lines: list[str] = []
    for path in sorted((kb_dir / "wiki" / "concepts").glob("*.md")):
        desc = summarize_markdown(path)
        lines.append(f"- [[concepts/{path.stem}]] - {desc}".rstrip())
    return lines


def _exploration_lines(kb_dir: Path) -> list[str]:
    lines: list[str] = []
    for path in sorted((kb_dir / "wiki" / "explorations").glob("*.md")):
        desc = summarize_markdown(path)
        lines.append(f"- [[explorations/{path.stem}]] - {desc}".rstrip())
    return lines


def rebuild_index(kb_dir: Path) -> str:
    sections = [
        "# Knowledge Base Index",
        "",
        "## Documents",
        *(_document_lines(kb_dir) or [""]),
        "",
        "## Concepts",
        *(_concept_lines(kb_dir) or [""]),
        "",
        "## Explorations",
        *(_exploration_lines(kb_dir) or [""]),
        "",
    ]
    content = "\n".join(sections).replace("\n\n\n", "\n\n")
    (kb_dir / "wiki" / "index.md").write_text(content, encoding="utf-8")
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild wiki/index.md for an agent-native KB.")
    parser.add_argument("kb_dir", nargs="?", default=".", help="Knowledge base root directory")
    args = parser.parse_args()
    print(rebuild_index(Path(args.kb_dir).resolve()))


if __name__ == "__main__":
    main()
