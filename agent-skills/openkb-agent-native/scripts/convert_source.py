from __future__ import annotations

import argparse
import json
from pathlib import Path

from _converter import convert_document
from _images import convert_pdf_with_images
from tree_index import build_tree_index


def convert_source_file(source_path: Path, kb_dir: Path) -> dict:
    result = convert_document(source_path, kb_dir)
    resolved_source_path = result.source_path
    tree_index_path: str | None = None

    # Agent-native KBs do not rely on PageIndex. If a long PDF is detected but
    # conversion did not produce a markdown source, force a local markdown
    # fallback so downstream summary/query workflows still have source material.
    if (
        source_path.suffix.lower() == ".pdf"
        and result.is_long_doc
        and result.raw_path is not None
        and resolved_source_path is None
    ):
        doc_name = result.raw_path.stem
        sources_dir = kb_dir / "wiki" / "sources"
        images_dir = sources_dir / "images" / doc_name
        markdown = convert_pdf_with_images(result.raw_path, doc_name, images_dir)
        resolved_source_path = sources_dir / f"{doc_name}.md"
        resolved_source_path.write_text(markdown, encoding="utf-8")

    if result.is_long_doc and resolved_source_path is not None:
        tree_index = build_tree_index(resolved_source_path, kb_dir)
        tree_index_path = tree_index["tree_index_path"]

    return {
        "raw_path": result.raw_path.as_posix() if result.raw_path else None,
        "source_path": resolved_source_path.as_posix() if resolved_source_path else None,
        "is_long_doc": result.is_long_doc,
        "skipped": result.skipped,
        "file_hash": result.file_hash,
        "tree_index_path": tree_index_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a source file into an OpenKB-compatible source artifact.")
    parser.add_argument("source_path", help="Source file to convert")
    parser.add_argument("--kb-dir", required=True, help="Knowledge base root directory")
    args = parser.parse_args()

    result = convert_source_file(Path(args.source_path).resolve(), Path(args.kb_dir).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
