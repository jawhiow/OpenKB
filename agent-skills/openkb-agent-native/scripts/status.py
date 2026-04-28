from __future__ import annotations

import argparse
from pathlib import Path

from _common import load_json


def _count_files(path: Path, *patterns: str) -> int:
    total = 0
    for pattern in patterns:
        total += len(list(path.glob(pattern)))
    return total


def collect_status(kb_dir: Path) -> dict:
    directories = {
        "sources": _count_files(kb_dir / "wiki" / "sources", "*.md", "*.json"),
        "summaries": _count_files(kb_dir / "wiki" / "summaries", "*.md"),
        "concepts": _count_files(kb_dir / "wiki" / "concepts", "*.md"),
        "explorations": _count_files(kb_dir / "wiki" / "explorations", "*.md"),
        "reports": _count_files(kb_dir / "wiki" / "reports", "*.md"),
        "raw": len([p for p in (kb_dir / "raw").rglob("*") if p.is_file()]),
    }
    hashes = load_json(kb_dir / ".openkb" / "hashes.json", {})
    return {
        "directories": directories,
        "total_indexed": len(hashes),
    }


def render_status(kb_dir: Path) -> str:
    status = collect_status(kb_dir)
    lines = ["Knowledge Base Status:"]
    for name, count in status["directories"].items():
        lines.append(f"- {name}: {count}")
    lines.append(f"- total_indexed: {status['total_indexed']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Show high-level KB status.")
    parser.add_argument("kb_dir", nargs="?", default=".", help="Knowledge base root directory")
    args = parser.parse_args()
    print(render_status(Path(args.kb_dir).resolve()))


if __name__ == "__main__":
    main()
