from __future__ import annotations


def build_page_chunks(total_pages: int, *, chunk_size: int = 100) -> list[dict[str, int]]:
    """Split *total_pages* into ordered page chunks of at most *chunk_size*."""
    total_pages = max(int(total_pages), 0)
    chunk_size = max(int(chunk_size), 1)
    if total_pages == 0:
        return []

    chunks: list[dict[str, int]] = []
    start_page = 1
    index = 1
    while start_page <= total_pages:
        end_page = min(start_page + chunk_size - 1, total_pages)
        chunks.append(
            {
                "index": index,
                "start_page": start_page,
                "end_page": end_page,
                "page_count": end_page - start_page + 1,
            }
        )
        start_page = end_page + 1
        index += 1
    return chunks
