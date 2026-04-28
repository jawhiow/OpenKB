from __future__ import annotations

import argparse
from pathlib import Path

from _common import ensure_kb_structure, write_default_files


def create_kb(kb_dir: Path, *, language: str = "zh") -> Path:
    ensure_kb_structure(kb_dir)
    write_default_files(kb_dir, language=language)
    return kb_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize an agent-native OpenKB-compatible knowledge base.")
    parser.add_argument("kb_dir", nargs="?", default=".", help="Knowledge base root directory")
    parser.add_argument("--language", default="zh", help="Default KB language")
    args = parser.parse_args()

    kb_dir = Path(args.kb_dir).resolve()
    create_kb(kb_dir, language=args.language)
    print(kb_dir)


if __name__ == "__main__":
    main()
