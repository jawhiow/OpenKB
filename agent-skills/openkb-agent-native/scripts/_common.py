from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from _schema import AGENTS_MD


DEFAULT_CONFIG: dict[str, Any] = {
    "model": "agent-native",
    "language": "zh",
    "pageindex_threshold": 20,
    "agent_native": True,
}

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".md",
    ".markdown",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".txt",
    ".csv",
}


def ensure_kb_structure(kb_dir: Path) -> None:
    (kb_dir / "raw").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "explorations").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True, exist_ok=True)
    (kb_dir / ".openkb").mkdir(parents=True, exist_ok=True)


def write_default_files(kb_dir: Path, *, language: str = "zh") -> None:
    config = dict(DEFAULT_CONFIG)
    config["language"] = language
    dump_yaml(kb_dir / ".openkb" / "config.yaml", config)
    dump_json(kb_dir / ".openkb" / "hashes.json", {})

    (kb_dir / "wiki" / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")


def load_yaml(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else dict(default)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if default is None:
        return loaded
    merged = dict(default)
    merged.update(loaded)
    return merged


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def relative_to_kb(path: Path, kb_dir: Path) -> str:
    return path.resolve().relative_to(kb_dir.resolve()).as_posix()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text

    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text

    raw_meta = parts[0][4:]
    meta = yaml.safe_load(raw_meta) or {}
    return meta, parts[1]


def parse_markdown_file(path: Path) -> tuple[dict[str, Any], str]:
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def first_content_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped == "---":
            continue
        return stripped
    return ""


def summarize_markdown(path: Path) -> str:
    meta, body = parse_markdown_file(path)
    for key in ("brief", "summary", "description"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return first_content_line(body)


def append_log(kb_dir: Path, operation: str, description: str) -> None:
    log_path = kb_dir / "wiki" / "log.md"
    if not log_path.exists():
        log_path.write_text("# Operations Log\n\n", encoding="utf-8")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"## [{timestamp}] {operation} | {description}\n\n")


def ensure_md_target(wiki_dir: Path, target: str) -> Path:
    cleaned = target.strip()
    if cleaned.endswith(".md") or cleaned.endswith(".json"):
        return wiki_dir / cleaned
    return wiki_dir / f"{cleaned}.md"
