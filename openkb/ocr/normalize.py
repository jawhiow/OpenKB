from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def _image_target_path(doc_name: str, page_num: int, image_key: str) -> str:
    clean_key = PurePosixPath(str(image_key).replace("\\", "/")).as_posix().lstrip("/")
    return f"sources/images/{doc_name}/ocr/page-{page_num}/{clean_key}"


def normalize_ocr_payloads(payloads: list[dict[str, Any]], *, doc_name: str) -> tuple[list[dict[str, Any]], str]:
    """Normalize PaddleOCR JSONL payloads into OpenKB page JSON and Markdown."""
    pages: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    page_num = 0

    for payload in payloads:
        result = payload.get("result") if isinstance(payload, dict) else None
        layout_results = result.get("layoutParsingResults", []) if isinstance(result, dict) else []
        if not isinstance(layout_results, list):
            continue

        for entry in layout_results:
            markdown = entry.get("markdown", {}) if isinstance(entry, dict) else {}
            if not isinstance(markdown, dict):
                continue
            text = str(markdown.get("text") or "").strip()
            image_map = markdown.get("images", {})
            if not text and not image_map:
                continue

            page_num += 1
            images: list[dict[str, str]] = []
            rewritten_text = text
            if isinstance(image_map, dict):
                for image_key, image_url in image_map.items():
                    target_path = _image_target_path(doc_name, page_num, str(image_key))
                    rewritten_text = rewritten_text.replace(str(image_key), target_path)
                    images.append({"path": target_path, "url": str(image_url)})

            pages.append(
                {
                    "page": page_num,
                    "content": rewritten_text,
                    "images": images,
                }
            )
            markdown_parts.append(f"## Page {page_num}\n\n{rewritten_text}")

    return pages, "\n\n".join(markdown_parts).strip()
