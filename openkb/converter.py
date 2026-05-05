"""Document conversion pipeline for OpenKB."""
from __future__ import annotations

import inspect
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from markitdown import MarkItDown

from openkb.config import load_config
from openkb.images import (
    copy_relative_images,
    extract_base64_images,
    convert_pdf_to_pages,
    convert_pdf_with_images,
)
from openkb.ocr.detect import is_probably_scanned_pdf
from openkb.ocr.pipeline import prepare_ocr_artifacts
from openkb.pdf_strategy import recommend_long_pdf_strategy, resolve_long_pdf_strategy
from openkb.state import HashRegistry

logger = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    """Result returned by :func:`convert_document`."""

    raw_path: Path | None = None
    source_path: Path | None = None
    is_long_doc: bool = False
    local_long_doc: bool = False
    scan_detected: bool = False
    recommended_strategy: str = ""
    selected_strategy: str = ""
    pageindex_input_path: Path | None = None
    skipped: bool = False
    file_hash: str | None = None  # For deferred hash registration


def get_pdf_page_count(path: Path) -> int:
    """Return the number of pages in the PDF at *path* using pymupdf."""
    with pymupdf.open(str(path)) as doc:
        return doc.page_count


def _pageindex_long_doc_available() -> bool:
    """Return whether long-PDF indexing is available in the current environment."""
    try:
        from pageindex import PageIndexClient
    except ImportError:
        return False

    # Cloud SDK path: requires an explicit API key.
    if os.environ.get("PAGEINDEX_API_KEY", "").strip():
        return True

    # Local/open-source variants expose richer indexing methods.
    if hasattr(PageIndexClient, "collection") or hasattr(PageIndexClient, "index"):
        return True

    try:
        init_sig = inspect.signature(PageIndexClient.__init__)
    except (TypeError, ValueError):
        return False

    return any(
        name in init_sig.parameters
        for name in ("model", "workspace", "storage_path", "retrieve_model")
    )


def convert_document(
    src: Path,
    kb_dir: Path,
    *,
    force: bool = False,
    strategy_override: str | None = None,
    job=None,
) -> ConvertResult:
    """Convert a document and integrate it into the knowledge base.

    Steps:
    1. Hash-check — skip if already known.
    2. Copy source to ``raw/``.
    3. If PDF and page count >= threshold → return :attr:`ConvertResult.is_long_doc`.
    4. If ``.md`` — read, process relative images, save to ``wiki/sources/``.
    5. Otherwise — run MarkItDown, extract base64 images, save to ``wiki/sources/``.
    6. Register hash in the registry.
    """
    # ------------------------------------------------------------------
    # Load config & state
    # ------------------------------------------------------------------
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    threshold: int = config.get("pageindex_threshold", 20)
    registry = HashRegistry(openkb_dir / "hashes.json")

    # ------------------------------------------------------------------
    # 1. Hash check
    # ------------------------------------------------------------------
    file_hash = HashRegistry.hash_file(src)
    if registry.is_known(file_hash) and not force:
        logger.info("Skipping already-known file: %s", src.name)
        return ConvertResult(skipped=True)

    # ------------------------------------------------------------------
    # 2. Copy to raw/
    # ------------------------------------------------------------------
    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_dest = raw_dir / src.name
    if raw_dest.resolve() != src.resolve():
        shutil.copy2(src, raw_dest)

    # ------------------------------------------------------------------
    # 3. PDF long-doc detection
    # ------------------------------------------------------------------
    doc_name = src.stem

    if src.suffix.lower() == ".pdf":
        page_count = get_pdf_page_count(src)
        if page_count >= threshold:
            scan_detected = bool(is_probably_scanned_pdf(src))
            recommended_strategy = recommend_long_pdf_strategy(
                is_scanned=scan_detected,
                ocr_enabled=bool(config.get("ocr_enabled", True)),
                ocr_auto_recommend=bool(config.get("ocr_auto_recommend", True)),
                pageindex_local_enabled=bool(config.get("pageindex_local_enabled", False)),
            )
            selected_strategy = resolve_long_pdf_strategy(recommended_strategy, strategy_override)
            if selected_strategy in {"ocr-local-long", "ocr-pageindex-local"}:
                sources_dir = kb_dir / "wiki" / "sources"
                sources_dir.mkdir(parents=True, exist_ok=True)
                artifacts = prepare_ocr_artifacts(src, kb_dir, doc_name, file_hash, force=force, job=job)
                dest_json = sources_dir / f"{doc_name}.json"
                dest_json.write_text(artifacts["pages_path"].read_text(encoding="utf-8"), encoding="utf-8")
                return ConvertResult(
                    raw_path=raw_dest,
                    source_path=dest_json,
                    is_long_doc=True,
                    local_long_doc=selected_strategy == "ocr-local-long",
                    scan_detected=scan_detected,
                    recommended_strategy=recommended_strategy,
                    selected_strategy=selected_strategy,
                    pageindex_input_path=artifacts["pageindex_input_path"]
                    if selected_strategy == "ocr-pageindex-local"
                    else None,
                    file_hash=file_hash,
                )
            if _pageindex_long_doc_available():
                logger.info(
                    "Long PDF detected (%d pages >= %d threshold): %s",
                    page_count,
                    threshold,
                    src.name,
                )
                return ConvertResult(
                    raw_path=raw_dest,
                    is_long_doc=True,
                    scan_detected=scan_detected,
                    recommended_strategy=recommended_strategy,
                    selected_strategy=selected_strategy,
                    file_hash=file_hash,
                )

            logger.info(
                "Long PDF detected but no compatible PageIndex long-doc backend is available; "
                "using local page JSON fallback: %s",
                src.name,
            )
            sources_dir = kb_dir / "wiki" / "sources"
            sources_dir.mkdir(parents=True, exist_ok=True)
            images_dir = kb_dir / "wiki" / "sources" / "images" / doc_name
            images_dir.mkdir(parents=True, exist_ok=True)
            pages = convert_pdf_to_pages(src, doc_name, images_dir)
            dest_json = sources_dir / f"{doc_name}.json"
            dest_json.write_text(
                json.dumps(pages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return ConvertResult(
                raw_path=raw_dest,
                source_path=dest_json,
                is_long_doc=True,
                local_long_doc=True,
                scan_detected=scan_detected,
                recommended_strategy=recommended_strategy,
                selected_strategy=selected_strategy,
                file_hash=file_hash,
            )

    # ------------------------------------------------------------------
    # 4/5. Convert to Markdown
    # ------------------------------------------------------------------
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    images_dir = kb_dir / "wiki" / "sources" / "images" / src.stem
    images_dir.mkdir(parents=True, exist_ok=True)

    if src.suffix.lower() == ".md":
        markdown = src.read_text(encoding="utf-8")
        markdown = copy_relative_images(markdown, src.parent, doc_name, images_dir)
    elif src.suffix.lower() == ".pdf":
        # Use pymupdf dict-mode for PDFs: text + images inline at correct positions
        markdown = convert_pdf_with_images(src, doc_name, images_dir)
    else:
        # Non-PDF, non-MD: use markitdown (docx, pptx, html, etc.)
        mid = MarkItDown()
        result = mid.convert(str(src))
        markdown = result.text_content
        markdown = extract_base64_images(markdown, doc_name, images_dir)

    dest_md = sources_dir / f"{doc_name}.md"
    dest_md.write_text(markdown, encoding="utf-8")

    return ConvertResult(raw_path=raw_dest, source_path=dest_md, file_hash=file_hash)
