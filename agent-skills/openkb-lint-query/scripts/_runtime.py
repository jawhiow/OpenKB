from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


CONTENT_DIRS = {"summaries", "companies", "industries", "concepts", "explorations"}
AUTO_CREATE_DIRS = {"concepts", "companies", "industries", "explorations"}
EXCLUDED_MD = {"AGENTS.md", "SCHEMA.md", "log.md"}
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.%+-]*|[\u4e00-\u9fff]+", re.IGNORECASE)


def emit_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def find_kb_root(cwd: str | Path) -> Path | None:
    start = Path(cwd).resolve()
    chain = [start, *start.parents]

    for current in chain:
        if current.name.lower() == "wiki" and (
            (current / "index.md").exists()
            or (current / "AGENTS.md").exists()
            or any((current / name).exists() for name in CONTENT_DIRS)
        ):
            return current.parent

    for current in chain:
        wiki = current / "wiki"
        if wiki.exists() and wiki.is_dir() and (
            (current / ".openkb").exists()
            or (current / "raw").exists()
            or (wiki / "index.md").exists()
            or (wiki / "AGENTS.md").exists()
        ):
            return current
    return None


def resolve_kb(path: str | Path) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    supplied = Path(path).resolve()
    root = find_kb_root(supplied)
    if root is None and (supplied / "wiki").exists():
        root = supplied
    if root is None:
        warnings.append("No OpenKB knowledge base root found from the supplied path.")
        return None, warnings
    if not (root / "wiki").exists():
        warnings.append("Knowledge base root found, but wiki/ is missing.")
    return root, warnings


def wiki_root(kb_root: Path) -> Path:
    return kb_root / "wiki"


def safe_join(root: Path, relative_path: str) -> Path:
    raw = relative_path.replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ValueError("Path is empty.")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed.")
    if any(part in {"..", ""} for part in candidate.parts):
        raise ValueError("Path traversal is not allowed.")
    full = (root / candidate).resolve()
    if not full.is_relative_to(root.resolve()):
        raise ValueError("Path escapes the knowledge base.")
    return full


def tool_available(command: str) -> bool:
    return shutil.which(command) is not None


def package_available(name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def git_status(root: Path) -> dict[str, Any]:
    if not tool_available("git"):
        return {"is_git_repo": False, "status": "", "error": "git command not available"}
    try:
        inside = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
            timeout=5,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {"is_git_repo": False, "status": "", "error": ""}
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            text=True,
            capture_output=True,
            timeout=5,
        )
        return {
            "is_git_repo": True,
            "status": status.stdout.strip(),
            "error": status.stderr.strip(),
        }
    except Exception as exc:
        return {"is_git_repo": False, "status": "", "error": str(exc)}


def simple_config(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not path.exists():
        return result
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and not key.startswith("api_key") and "secret" not in key.lower():
            result[key] = value
    return result


def config_language(kb_root: Path) -> str:
    return str(simple_config(kb_root / ".openkb" / "config.yaml").get("language") or "en")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    metadata: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return metadata, "\n".join(lines[index + 1 :])
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip("'\"")
    return {}, text


def title_from_text(text: str, fallback: str) -> str:
    _, body = split_frontmatter(text)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def brief_from_text(text: str) -> str:
    metadata, body = split_frontmatter(text)
    brief = metadata.get("brief", "").strip()
    if brief:
        return brief
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        return stripped[:220].strip()
    return ""


def markdown_pages(wiki: Path, *, content_only: bool = True) -> list[Path]:
    if not wiki.exists():
        return []
    pages: list[Path] = []
    for path in sorted(wiki.rglob("*.md")):
        if not path.is_file() or path.name in EXCLUDED_MD:
            continue
        rel_parts = path.relative_to(wiki).parts
        if not rel_parts:
            continue
        first = rel_parts[0]
        if first in {"reports", "sources"}:
            continue
        if content_only and first not in CONTENT_DIRS and path.name != "index.md":
            continue
        pages.append(path)
    return pages


def relative_page_id(wiki: Path, path: Path) -> str:
    return path.relative_to(wiki).with_suffix("").as_posix()


def page_id_map(wiki: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for path in markdown_pages(wiki, content_only=False):
        rel = relative_page_id(wiki, path)
        mapping[rel] = path
        mapping[path.stem] = path
    return mapping


def normalize_wikilink_target(raw: str) -> str:
    target = raw.split("|", 1)[0].split("#", 1)[0].strip().strip("/")
    if target.endswith(".md"):
        target = target[:-3]
    return target.replace("\\", "/")


def wikilinks(text: str) -> list[str]:
    return [normalize_wikilink_target(match) for match in WIKILINK_RE.findall(text)]


def tokenize(text: str) -> list[str]:
    terms: list[str] = []
    for raw in WORD_RE.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            if len(raw) == 1:
                terms.append(raw)
            else:
                terms.append(raw)
                terms.extend(raw[index : index + 2] for index in range(len(raw) - 1))
        elif len(raw) >= 2:
            terms.append(raw)
    return terms


def score_text(text: str, query_terms: list[str], phrase: str) -> int:
    if not query_terms:
        return 0
    counts = Counter(tokenize(text))
    score = sum(counts.get(term, 0) for term in query_terms)
    normalized = " ".join(text.lower().split())
    if phrase and phrase in normalized:
        score += max(8, len(query_terms) * 4)
    return score


def snippet(text: str, query_terms: list[str], max_chars: int = 360) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    lower = compact.lower()
    first_hit = min(
        (index for term in query_terms for index in [lower.find(term)] if index >= 0),
        default=0,
    )
    start = max(0, first_hit - max_chars // 3)
    end = min(len(compact), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def slugify(value: str, default: str = "page") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value, flags=re.IGNORECASE)
    value = value.strip("-")
    return value[:80] or default


def link_for_page(page_path: str) -> str:
    return page_path[:-3] if page_path.endswith(".md") else page_path


def heading_for_page(page_path: str) -> str:
    first = page_path.split("/", 1)[0] if "/" in page_path else ""
    return {
        "summaries": "## Documents",
        "companies": "## Companies",
        "industries": "## Industries",
        "concepts": "## Concepts",
        "explorations": "## Explorations",
    }.get(first, "## Concepts")


def ensure_index_entry(wiki: Path, page_path: str, title: str, label: str = "") -> bool:
    index_path = wiki / "index.md"
    if index_path.exists():
        lines = read_text(index_path).splitlines()
    else:
        lines = ["# Knowledge Base Index", "", "## Documents", "", "## Companies", "", "## Industries", "", "## Concepts", "", "## Explorations"]

    heading = heading_for_page(page_path)
    if heading not in lines:
        insert_before = "## Explorations" if heading != "## Explorations" else ""
        if insert_before and insert_before in lines:
            at = lines.index(insert_before)
        else:
            at = len(lines)
        if at > 0 and lines[at - 1] != "":
            lines.insert(at, "")
            at += 1
        lines[at:at] = [heading, ""]

    link = f"[[{link_for_page(page_path)}]]"
    if any(link in line for line in lines):
        return False
    suffix = f" ({label})" if label else ""
    entry = f"- {link} - {title}{suffix}"
    insert_at = lines.index(heading) + 1
    lines.insert(insert_at, entry)
    write_text(index_path, "\n".join(lines).rstrip() + "\n")
    return True


def append_log(wiki: Path, operation: str, description: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = wiki / "log.md"
    existing = read_text(path) if path.exists() else "# Log\n\n"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    write_text(path, f"{existing}## [{timestamp}] {operation} | {description}\n")


def report_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_evidence_map(wiki: Path) -> dict[str, Any]:
    path = wiki / "evidence_map.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(read_text(path))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_sources_field(text: str) -> list[str]:
    metadata, _ = split_frontmatter(text)
    raw = metadata.get("sources", "").strip()
    if not raw.startswith("[") or not raw.endswith("]"):
        return []
    return [item.strip().strip("'\"") for item in raw[1:-1].split(",") if item.strip()]


def draft_page(title: str, page_type: str, reason: str, source_link: str = "") -> str:
    source_line = (
        f"- [[{source_link}]]: Seed evidence for this draft."
        if source_link
        else "- Source evidence has not been attached yet; keep this page in draft status until citations are added."
    )
    return (
        "---\n"
        "sources: []\n"
        f"brief: Draft page for {title}.\n"
        "status: draft\n"
        "generated_by: openkb-lint-query\n"
        "---\n\n"
        f"# {title}\n\n"
        f"This draft was created from a lint finding: {reason}\n\n"
        "## Why It Matters\n"
        f"Draft note: explain why this {page_type} matters in the knowledge base.\n\n"
        "## Source Evidence\n"
        f"{source_line}\n\n"
        "## Key Points To Track\n"
        "Draft note: add durable claims, metrics, catalysts, mechanisms, or monitoring indicators.\n\n"
        "## Risks And Contra-Evidence\n"
        "Draft note: add evidence that could weaken or contradict the page thesis.\n\n"
        "## Related Pages\n"
        "Draft note: link related summaries, companies, industries, concepts, or explorations.\n"
    )


def safe_markdown_path(wiki: Path, page_path: str) -> tuple[Path, str] | None:
    raw = page_path.replace("\\", "/").strip().lstrip("/")
    if not raw.endswith(".md"):
        raw += ".md"
    first = raw.split("/", 1)[0]
    if first not in AUTO_CREATE_DIRS:
        return None
    try:
        full = safe_join(wiki, raw)
    except ValueError:
        return None
    return full, raw
