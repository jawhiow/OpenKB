from __future__ import annotations

from typing import Any


def _as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _first_text(mapping: dict[str, Any], keys: tuple[str, ...], fallback: str = "") -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def _normalize_nodes(nodes: Any, prefix: str = "local") -> list[dict[str, Any]]:
    if isinstance(nodes, dict):
        nodes = nodes.get("children") or nodes.get("nodes") or nodes.get("structure") or []
    if not isinstance(nodes, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, raw_node in enumerate(nodes, start=1):
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("node_id") or raw_node.get("id") or f"{prefix}-{index}")
        children = raw_node.get("nodes")
        if children is None:
            children = raw_node.get("children")
        normalized.append(
            {
                "title": _first_text(raw_node, ("title", "heading", "name"), f"Section {index}"),
                "node_id": node_id,
                "start_index": _as_int(
                    raw_node.get("start_index", raw_node.get("page_start", raw_node.get("start_page"))),
                    index,
                ),
                "end_index": _as_int(
                    raw_node.get("end_index", raw_node.get("page_end", raw_node.get("end_page"))),
                    _as_int(raw_node.get("start_index", raw_node.get("page_start", raw_node.get("start_page"))), index),
                ),
                "summary": _first_text(raw_node, ("summary", "node_summary", "description", "text")),
                "nodes": _normalize_nodes(children, node_id),
            }
        )
    return normalized


def normalize_pageindex_local_tree(payload: dict[str, Any], *, fallback_doc_name: str) -> dict[str, Any]:
    """Normalize local PageIndex output to OpenKB's PageIndex tree shape."""
    doc = payload.get("doc") if isinstance(payload.get("doc"), dict) else {}
    structure = payload.get("structure")
    if structure is None:
        structure = payload.get("tree")
    if structure is None and isinstance(payload.get("result"), dict):
        result = payload["result"]
        structure = result.get("structure") or result.get("tree")
        if not doc and isinstance(result.get("doc"), dict):
            doc = result["doc"]
    elif structure is None:
        structure = payload.get("result")

    return {
        "doc_name": _first_text(payload, ("doc_name", "name"), _first_text(doc, ("doc_name", "name"), fallback_doc_name)),
        "doc_description": _first_text(
            payload,
            ("doc_description", "description"),
            _first_text(doc, ("doc_description", "description"), ""),
        ),
        "structure": _normalize_nodes(structure),
    }


def pageindex_local_doc_id(payload: dict[str, Any], *, fallback_doc_name: str) -> str:
    """Return a stable document id from local PageIndex output."""
    doc = payload.get("doc") if isinstance(payload.get("doc"), dict) else {}
    value = payload.get("doc_id") or payload.get("id") or doc.get("doc_id") or doc.get("id")
    return str(value or f"pageindex-local-{fallback_doc_name}")

