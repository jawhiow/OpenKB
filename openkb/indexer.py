"""PageIndex indexer for long documents."""
from __future__ import annotations

import json as json_mod
import logging
import os
import time

from dataclasses import dataclass
from pathlib import Path

from pageindex import PageIndexClient

try:
    from pageindex import IndexConfig
except ImportError:
    @dataclass
    class IndexConfig:
        """Fallback config object for PageIndex versions without IndexConfig."""

        if_add_node_text: bool
        if_add_node_summary: bool
        if_add_doc_description: bool

from openkb.config import load_config
from openkb.tree_renderer import render_summary_md

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Result of indexing a long document via PageIndex."""

    doc_id: str
    description: str
    tree: dict


def _load_json_payload(payload):
    """Normalize PageIndex responses that may be JSON strings or Python objects."""
    if isinstance(payload, str):
        return json_mod.loads(payload)
    return payload


def _is_cloud_result_ready(payload: dict) -> bool:
    """Return whether a PageIndex cloud API response is complete."""
    status = str(payload.get("status", "")).lower()
    if status in {"completed", "complete", "done", "success", "succeeded", "ready"}:
        return True
    if payload.get("retrieval_ready") is True:
        return True
    return "result" in payload and payload.get("result") not in (None, "")


def _poll_cloud_result(fetch_fn, label: str, max_attempts: int = 60, sleep_seconds: float = 2.0) -> dict:
    """Poll a PageIndex cloud operation until the response is ready."""
    last_payload: dict | None = None
    for attempt in range(1, max_attempts + 1):
        payload = _load_json_payload(fetch_fn())
        last_payload = payload
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected {label} response type: {type(payload).__name__}")
        if _is_cloud_result_ready(payload):
            return payload
        logger.info("Waiting for PageIndex %s to complete (attempt %d/%d)", label, attempt, max_attempts)
        time.sleep(sleep_seconds)
    raise RuntimeError(f"Timed out waiting for PageIndex {label}: {last_payload}")


def _extract_tree_structure(payload: dict) -> list:
    """Extract a tree structure list from any supported PageIndex payload shape."""
    result = payload.get("result", payload.get("tree", payload.get("structure", [])))
    if isinstance(result, dict):
        result = result.get("structure", result.get("tree", []))
    return result if isinstance(result, list) else []


def _normalize_cloud_ocr_pages(payload: dict) -> list[dict]:
    """Normalize cloud OCR responses to OpenKB's per-page JSON structure."""
    raw_pages = payload.get("result", payload.get("pages", payload))
    if isinstance(raw_pages, dict):
        raw_pages = raw_pages.get("pages", [])
    if not isinstance(raw_pages, list):
        return []

    normalized: list[dict] = []
    for idx, entry in enumerate(raw_pages, start=1):
        if isinstance(entry, str):
            normalized.append({"page": idx, "content": entry, "images": []})
            continue
        if not isinstance(entry, dict):
            continue
        normalized.append({
            "page": entry.get("page") or entry.get("page_number") or idx,
            "content": entry.get("content") or entry.get("markdown") or entry.get("text") or "",
            "images": entry.get("images") if isinstance(entry.get("images"), list) else [],
        })
    return normalized


def index_long_document(pdf_path: Path, kb_dir: Path) -> IndexResult:
    """Index a long PDF document using PageIndex and write wiki pages."""
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")

    model: str = config.get("model", "gpt-5.4")
    pageindex_api_key = os.environ.get("PAGEINDEX_API_KEY", "")

    index_config = IndexConfig(
        if_add_node_text=True,
        if_add_node_summary=True,
        if_add_doc_description=True,
    )

    col = None

    try:
        client = PageIndexClient(
            api_key=pageindex_api_key or None,
            model=model,
            storage_path=str(openkb_dir),
            index_config=index_config,
        )
        col = client.collection()

        # Newer PageIndex clients expose a collection-style API.
        max_retries = 3
        doc_id = None
        for attempt in range(1, max_retries + 1):
            try:
                doc_id = col.add(str(pdf_path))
                logger.info("PageIndex added %s -> doc_id=%s (attempt %d)", pdf_path.name, doc_id, attempt)
                break
            except Exception as exc:
                logger.warning("PageIndex attempt %d/%d failed for %s: %s", attempt, max_retries, pdf_path.name, exc)
                if attempt == max_retries:
                    raise RuntimeError(f"Failed to index {pdf_path.name} after {max_retries} attempts: {exc}") from exc

        doc = col.get_document(doc_id, include_text=True)
        doc_name: str = doc.get("doc_name", pdf_path.stem)
        description: str = doc.get("doc_description", "")
        structure: list = doc.get("structure", [])
    except TypeError:
        try:
            # Older local/open-source client API.
            client = PageIndexClient(
                api_key=pageindex_api_key or None,
                model=model,
                workspace=str(openkb_dir),
            )
            doc_id = client.index(str(pdf_path), mode="pdf")
            doc = _load_json_payload(client.get_document(doc_id))
            structure = _load_json_payload(client.get_document_structure(doc_id))
            doc_name = doc.get("doc_name", pdf_path.stem)
            description = doc.get("doc_description", "")
            if isinstance(structure, dict):
                structure = structure.get("structure", [])
        except (AttributeError, TypeError):
            # Current PyPI package is a cloud SDK with submit_document/get_tree/get_ocr.
            if not pageindex_api_key:
                raise RuntimeError(
                    "Current PageIndex installation only supports cloud indexing. "
                    "Set PAGEINDEX_API_KEY or use a local PageIndex-compatible package."
                )

            client = PageIndexClient(pageindex_api_key)
            submission = _load_json_payload(client.submit_document(str(pdf_path)))
            doc_id = submission.get("doc_id") or submission.get("id")
            if not doc_id:
                raise RuntimeError(f"PageIndex submit_document returned no doc_id: {submission}")

            tree_payload = _poll_cloud_result(
                lambda: client.get_tree(doc_id, node_summary=True),
                "tree generation",
            )
            doc = _load_json_payload(client.get_document(doc_id))
            structure = _extract_tree_structure(tree_payload)
            doc_name = doc.get("doc_name") or doc.get("name") or pdf_path.stem
            description = doc.get("doc_description") or doc.get("description", "")
            all_pages = _normalize_cloud_ocr_pages(
                _poll_cloud_result(
                    lambda: client.get_ocr(doc_id, format="page"),
                    "OCR extraction",
                )
            )

    logger.info("Doc keys: %s", list(doc.keys()))
    logger.info("page_count from doc: %s", doc.get("page_count", "NOT PRESENT"))

    tree = {
        "doc_name": doc_name,
        "doc_description": description,
        "structure": structure,
    }

    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    images_dir = sources_dir / "images" / pdf_path.stem

    from openkb.images import convert_pdf_to_pages

    all_pages: list = locals().get("all_pages", [])
    if pageindex_api_key and col is not None:
        from openkb.converter import get_pdf_page_count

        page_count = get_pdf_page_count(pdf_path)
        try:
            all_pages = col.get_page_content(doc_id, f"1-{page_count}")
        except Exception as exc:
            logger.warning("Cloud get_page_content failed for %s: %s", pdf_path.name, exc)

    if not all_pages:
        if pageindex_api_key and col is not None:
            logger.warning("Cloud returned no pages for %s; falling back to local pymupdf", pdf_path.name)
        all_pages = convert_pdf_to_pages(pdf_path, pdf_path.stem, images_dir)

    (sources_dir / f"{pdf_path.stem}.json").write_text(
        json_mod.dumps(all_pages, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_md = render_summary_md(tree, pdf_path.stem, doc_id)
    (summaries_dir / f"{pdf_path.stem}.md").write_text(summary_md, encoding="utf-8")

    return IndexResult(doc_id=doc_id, description=description, tree=tree)
