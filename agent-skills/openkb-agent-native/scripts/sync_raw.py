from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import SUPPORTED_EXTENSIONS, relative_to_kb
from hash_registry import HashRegistry


def scan_pending(kb_dir: Path) -> list[dict]:
    registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    pending: list[dict] = []

    for path in sorted((kb_dir / "raw").rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        digest = registry.hash_file(path)
        if registry.is_known(digest):
            continue

        rel_raw = relative_to_kb(path, kb_dir)
        existing = registry.find_by_raw_path(rel_raw) or registry.find_by_name(path.name)
        reason = "changed" if existing else "new"
        pending.append(
            {
                "path": str(path),
                "raw_path": rel_raw,
                "hash": digest,
                "reason": reason,
            }
        )

    return pending


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan raw/ for pending or changed documents.")
    parser.add_argument("kb_dir", nargs="?", default=".", help="Knowledge base root directory")
    args = parser.parse_args()
    print(json.dumps(scan_pending(Path(args.kb_dir).resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
