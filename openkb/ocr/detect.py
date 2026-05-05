from __future__ import annotations

from pathlib import Path

import pymupdf


def _sample_page_indexes(page_count: int, sample_pages: int) -> list[int]:
    if page_count <= 0:
        return []
    if page_count <= sample_pages:
        return list(range(page_count))
    sample = {0, page_count - 1, page_count // 2}
    while len(sample) < sample_pages:
        sample.add(min(len(sample), page_count - 1))
    return sorted(sample)


def is_probably_scanned_pdf(
    pdf_path: Path,
    *,
    sample_pages: int = 3,
    min_average_text_chars: int = 20,
) -> bool:
    """Heuristically detect whether a PDF is likely scanned.

    A PDF is treated as scanned when the sampled pages contain very little
    extractable text from the native text layer.
    """
    with pymupdf.open(str(pdf_path)) as doc:
        indexes = _sample_page_indexes(len(doc), max(sample_pages, 1))
        if not indexes:
            return False
        text_lengths: list[int] = []
        for index in indexes:
            text = doc[index].get_text("text")
            text_lengths.append(len("".join(str(text).split())))
    average = sum(text_lengths) / len(text_lengths)
    return average < min_average_text_chars
