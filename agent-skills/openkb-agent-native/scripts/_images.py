"""Image extraction and copy utilities for the agent-native converter pipeline."""
from __future__ import annotations

import base64
import logging
import re
import shutil
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

_BASE64_RE = re.compile(r"!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)")
_RELATIVE_RE = re.compile(r"!\[([^\]]*)\]\((?!https?://|data:)([^)]+)\)")
_MIN_IMAGE_DIM = 32


def convert_pdf_with_images(pdf_path: Path, doc_name: str, images_dir: Path) -> str:
    """Convert a PDF to markdown with inline images using pymupdf dict-mode."""
    images_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    img_counter = 0

    with pymupdf.open(str(pdf_path)) as doc:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1
            parts.append("\n\n")

            for block in page.get_text("dict")["blocks"]:
                if block["type"] == 0:
                    lines = []
                    for line in block["lines"]:
                        spans_text = "".join(span["text"] for span in line["spans"])
                        lines.append(spans_text)
                    parts.append("\n".join(lines))

                elif block["type"] == 1:
                    width = block.get("width", 0)
                    height = block.get("height", 0)
                    if width < _MIN_IMAGE_DIM or height < _MIN_IMAGE_DIM:
                        continue
                    image_bytes = block.get("image")
                    if not image_bytes:
                        continue
                    try:
                        pix = pymupdf.Pixmap(image_bytes)
                        if pix.n > 4:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        img_counter += 1
                        filename = f"p{page_num}_img{img_counter}.png"
                        (images_dir / filename).write_bytes(pix.tobytes("png"))
                        pix = None
                        parts.append(f"\n![image](sources/images/{doc_name}/{filename})\n")
                    except Exception:
                        logger.warning("Failed to save image block on page %d", page_num)
    return "\n".join(parts)


def extract_base64_images(markdown: str, doc_name: str, images_dir: Path) -> str:
    """Decode base64-embedded images, save them, and rewrite markdown links."""
    counter = 0
    result = markdown

    for match in _BASE64_RE.finditer(markdown):
        alt, ext, b64_data = match.group(1), match.group(2), match.group(3)
        try:
            image_bytes = base64.b64decode(b64_data, validate=True)
        except Exception:
            logger.warning(
                "Failed to decode base64 image (alt=%r, ext=%r); leaving original.",
                alt,
                ext,
            )
            continue

        counter += 1
        filename = f"img_{counter:03d}.{ext}"
        dest = images_dir / filename
        images_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)

        new_ref = f"![{alt}](sources/images/{doc_name}/{filename})"
        result = result.replace(match.group(0), new_ref, 1)

    return result


def copy_relative_images(markdown: str, source_dir: Path, doc_name: str, images_dir: Path) -> str:
    """Copy locally-referenced images into the KB images directory and rewrite links."""
    result = markdown

    for match in _RELATIVE_RE.finditer(markdown):
        alt, rel_path = match.group(1), match.group(2)
        src = (source_dir / rel_path).resolve()
        if not src.is_relative_to(source_dir.resolve()):
            logger.warning("Image path escapes source dir: %s; skipping.", rel_path)
            continue
        if not src.exists():
            logger.warning("Relative image not found: %s; leaving original link.", src)
            continue

        filename = src.name
        dest = images_dir / filename
        images_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

        new_ref = f"![{alt}](sources/images/{doc_name}/{filename})"
        result = result.replace(match.group(0), new_ref, 1)

    return result
