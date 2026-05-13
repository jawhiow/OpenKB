from __future__ import annotations

from typing import Final


PLAIN_LOCAL_LONG: Final[str] = "plain-local-long"
OCR_LOCAL_LONG: Final[str] = "ocr-local-long"
OCR_PAGEINDEX_LOCAL: Final[str] = "ocr-pageindex-local"

_KNOWN_STRATEGIES = {PLAIN_LOCAL_LONG, OCR_LOCAL_LONG, OCR_PAGEINDEX_LOCAL}


def recommend_long_pdf_strategy(
    *,
    is_scanned: bool,
    ocr_enabled: bool,
    ocr_auto_recommend: bool,
    pageindex_local_enabled: bool,
) -> str:
    """Return the default long-PDF strategy for current document conditions."""
    del pageindex_local_enabled
    if is_scanned and ocr_enabled and ocr_auto_recommend:
        return OCR_PAGEINDEX_LOCAL
    return ""


def resolve_long_pdf_strategy(recommended: str, override: str | None) -> str:
    """Return the final strategy, preferring a valid explicit override."""
    if override and override in _KNOWN_STRATEGIES:
        return override
    return recommended
