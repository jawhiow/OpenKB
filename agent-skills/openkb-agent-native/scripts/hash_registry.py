from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from _common import dump_json, load_json


class HashRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = load_json(path, {})

    def is_known(self, file_hash: str) -> bool:
        return file_hash in self._data

    def get(self, file_hash: str) -> dict[str, Any] | None:
        return self._data.get(file_hash)

    def add(self, file_hash: str, metadata: dict[str, Any]) -> None:
        self._data[file_hash] = metadata
        self._persist()

    def remove(self, file_hash: str) -> None:
        if file_hash in self._data:
            del self._data[file_hash]
            self._persist()

    def all_entries(self) -> dict[str, dict[str, Any]]:
        return dict(self._data)

    def find_by_name(self, name: str) -> tuple[str, dict[str, Any]] | None:
        for file_hash, metadata in self._data.items():
            if metadata.get("name") == name:
                return file_hash, metadata
        return None

    def find_by_raw_path(self, raw_path: str) -> tuple[str, dict[str, Any]] | None:
        for file_hash, metadata in self._data.items():
            if metadata.get("raw_path") == raw_path:
                return file_hash, metadata
        return None

    def _persist(self) -> None:
        dump_json(self.path, self._data)

    @staticmethod
    def hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute or update KB hash registry.")
    parser.add_argument("registry", help="Path to hashes.json")
    parser.add_argument("--hash-file", dest="hash_file_path", help="File to hash")
    args = parser.parse_args()

    registry = HashRegistry(Path(args.registry))
    if args.hash_file_path:
        print(registry.hash_file(Path(args.hash_file_path)))
        return
    print(registry.all_entries())


if __name__ == "__main__":
    main()
