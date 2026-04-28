"""Self-contained document conversion pipeline for the agent-native skill."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
from pathlib import Path

import pymupdf
from markitdown import MarkItDown

from _common import DEFAULT_CONFIG, load_yaml
from _images import copy_relative_images, convert_pdf_with_images, extract_base64_images
from hash_registry import HashRegistry

logger = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    raw_path: Path | None = None
    source_path: Path | None = None
    is_long_doc: bool = False
    skipped: bool = False
    file_hash: str | None = None


def get_pdf_page_count(path: Path) -> int:
    with pymupdf.open(str(path)) as doc:
        return doc.page_count


def convert_document(src: Path, kb_dir: Path) -> ConvertResult:
    """Convert a document and integrate it into the knowledge base."""
    openkb_dir = kb_dir / ".openkb"
    config = load_yaml(openkb_dir / "config.yaml", DEFAULT_CONFIG)
    threshold: int = int(config.get("pageindex_threshold", DEFAULT_CONFIG["pageindex_threshold"]))
    registry = HashRegistry(openkb_dir / "hashes.json")

    file_hash = HashRegistry.hash_file(src)
    if registry.is_known(file_hash):
        logger.info("Skipping already-known file: %s", src.name)
        return ConvertResult(skipped=True)

    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_dest = raw_dir / src.name
    if raw_dest.resolve() != src.resolve():
        shutil.copy2(src, raw_dest)

    if src.suffix.lower() == ".pdf":
        page_count = get_pdf_page_count(src)
        if page_count >= threshold:
            return ConvertResult(raw_path=raw_dest, is_long_doc=True, file_hash=file_hash)

    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    images_dir = sources_dir / "images" / src.stem
    images_dir.mkdir(parents=True, exist_ok=True)
    doc_name = src.stem

    if src.suffix.lower() == ".md":
        markdown = src.read_text(encoding="utf-8")
        markdown = copy_relative_images(markdown, src.parent, doc_name, images_dir)
    elif src.suffix.lower() == ".pdf":
        markdown = convert_pdf_with_images(src, doc_name, images_dir)
    else:
        mid = MarkItDown()
        result = mid.convert(str(src))
        markdown = extract_base64_images(result.text_content, doc_name, images_dir)

    dest_md = sources_dir / f"{doc_name}.md"
    dest_md.write_text(markdown, encoding="utf-8")
    return ConvertResult(raw_path=raw_dest, source_path=dest_md, file_hash=file_hash)
