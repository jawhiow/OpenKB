from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import count
from pathlib import Path
from typing import Any

from _common import dump_json


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")


def tree_index_path_for_source(source_path: Path, kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "tree_index" / f"{source_path.stem}.json"


def _new_node(node_ids: count, title: str, level: int, start_line: int) -> dict[str, Any]:
    return {
        "title": title,
        "node_id": f"node-{next(node_ids)}",
        "level": level,
        "start_line": start_line,
        "end_line": start_line,
        "char_count": 0,
        "summary": "",
        "preview": "",
        "children": [],
    }


def _summarize_text(lines: list[str]) -> tuple[str, str, int]:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if IMAGE_LINE_RE.match(stripped):
            continue
        if len(stripped) < 3:
            continue
        cleaned.append(stripped)

    preview = " ".join(cleaned)[:280]
    summary = cleaned[0][:160] if cleaned else ""
    char_count = sum(len(line) for line in lines)
    return summary, preview, char_count


def _finalize_node(node: dict[str, Any], lines: list[str], end_line: int) -> None:
    node["end_line"] = max(end_line, node["start_line"])
    body_lines = lines[node["start_line"] : node["end_line"]]
    summary, preview, char_count = _summarize_text(body_lines)
    node["summary"] = summary
    node["preview"] = preview
    node["char_count"] = char_count


def _build_heading_tree(lines: list[str]) -> list[dict[str, Any]]:
    node_ids = count(1)
    roots: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []

    for idx, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line.strip())
        if not match:
            continue

        level = len(match.group(1))
        title = match.group(2).strip()
        node = _new_node(node_ids, title, level, idx)

        while stack and int(stack[-1]["level"]) >= level:
            popped = stack.pop()
            _finalize_node(popped, lines, idx - 1)

        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)

        stack.append(node)

    while stack:
        popped = stack.pop()
        _finalize_node(popped, lines, len(lines))

    return roots


def _build_chunk_tree(lines: list[str], doc_name: str, max_chars: int) -> list[dict[str, Any]]:
    node_ids = count(1)
    root = _new_node(node_ids, doc_name, 1, 1)
    root["children"] = []

    current_lines: list[str] = []
    current_start = 1
    current_chars = 0

    def flush_chunk(end_line: int) -> None:
        nonlocal current_lines, current_start, current_chars
        if not current_lines:
            return
        chunk = _new_node(node_ids, f"Chunk {len(root['children']) + 1}", 2, current_start)
        summary, preview, char_count = _summarize_text(current_lines)
        chunk["summary"] = summary
        chunk["preview"] = preview
        chunk["char_count"] = char_count
        chunk["start_line"] = current_start
        chunk["end_line"] = end_line
        root["children"].append(chunk)
        current_lines = []
        current_chars = 0

    for idx, line in enumerate(lines, start=1):
        if not current_lines:
            current_start = idx
        current_lines.append(line)
        current_chars += len(line)
        if current_chars >= max_chars and not line.strip():
            flush_chunk(idx)
        elif current_chars >= max_chars * 2:
            flush_chunk(idx)

    if current_lines:
        flush_chunk(len(lines))

    _finalize_node(root, lines, len(lines))
    return [root]


def build_tree_index(source_path: Path, kb_dir: Path, max_chars: int = 4000) -> dict[str, Any]:
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    structure = _build_heading_tree(lines)
    if not structure:
        structure = _build_chunk_tree(lines, source_path.stem, max_chars=max_chars)

    index = {
        "doc_name": source_path.stem,
        "doc_description": "",
        "source_path": source_path.as_posix(),
        "structure": structure,
    }

    output_path = tree_index_path_for_source(source_path, kb_dir)
    dump_json(output_path, index)
    index["tree_index_path"] = output_path.as_posix()
    return index


def _flatten_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten_nodes(node.get("children", [])))
    return out


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def search_tree_index(index: dict[str, Any], query: str, top_k: int = 5) -> list[dict[str, Any]]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    matches: list[dict[str, Any]] = []
    for node in _flatten_nodes(index.get("structure", [])):
        hay_title = _tokenize(str(node.get("title", "")))
        hay_summary = _tokenize(str(node.get("summary", "")))
        hay_preview = _tokenize(str(node.get("preview", "")))

        score = 0
        for token in query_tokens:
            score += hay_title.count(token) * 5
            score += hay_summary.count(token) * 3
            score += hay_preview.count(token)

        if node.get("children"):
            score = int(score * 0.5)

        if score > 0:
            matches.append(
                {
                    "node_id": node["node_id"],
                    "title": node["title"],
                    "score": score,
                    "start_line": node["start_line"],
                    "end_line": node["end_line"],
                    "summary": node.get("summary", ""),
                }
            )

    matches.sort(key=lambda item: (-item["score"], item["end_line"] - item["start_line"], item["start_line"]))
    return matches[:top_k]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Build or search a lightweight tree index for long sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("source_path")
    build_parser.add_argument("--kb-dir", required=True)
    build_parser.add_argument("--max-chars", type=int, default=4000)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("index_path")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    if args.command == "build":
        result = build_tree_index(Path(args.source_path).resolve(), Path(args.kb_dir).resolve(), max_chars=args.max_chars)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    index = json.loads(Path(args.index_path).read_text(encoding="utf-8"))
    print(json.dumps(search_tree_index(index, args.query, top_k=args.top_k), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
