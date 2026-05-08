from __future__ import annotations

import argparse
from pathlib import Path

from _runtime import append_log, emit_json, ensure_index_entry, read_text, resolve_kb, slugify, wiki_root, write_text


def unique_path(directory: Path, slug: str) -> Path:
    candidate = directory / f"{slug}.md"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = directory / f"{slug}-{index}.md"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not find a unique exploration filename.")


def save(kb: str, title: str, answer_file: str) -> dict:
    kb_root, warnings = resolve_kb(kb)
    if kb_root is None:
        return {"ok": False, "error": "No OpenKB knowledge base found.", "warnings": warnings}
    wiki = wiki_root(kb_root)
    answer_path = Path(answer_file).resolve()
    answer = read_text(answer_path)
    if not answer.strip():
        return {"ok": False, "error": "Answer file is empty.", "warnings": warnings}

    explorations = wiki / "explorations"
    explorations.mkdir(parents=True, exist_ok=True)
    slug = slugify(title, "exploration")
    path = unique_path(explorations, slug)
    rel = path.relative_to(wiki).as_posix()
    content = (
        "---\n"
        f"title: {title}\n"
        "generated_by: openkb-lint-query\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{answer.rstrip()}\n\n"
        "## Read Set\n"
        "Review note: keep or update the citations/read-set from the original answer.\n"
    )
    write_text(path, content)
    ensure_index_entry(wiki, rel, title, "saved exploration")
    append_log(wiki, "query", f"saved exploration -> {path.name}")
    return {
        "ok": True,
        "kb_root": str(kb_root),
        "wiki_root": str(wiki),
        "path": rel,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Save an explicitly approved query answer as an OpenKB exploration.")
    parser.add_argument("--kb", required=True, help="Knowledge base root or a directory inside it.")
    parser.add_argument("--title", required=True, help="Exploration title.")
    parser.add_argument("--answer", required=True, help="Markdown/text file containing the answer to save.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()
    data = save(args.kb, args.title, args.answer)
    if args.json:
        emit_json(data)
        return
    if not data.get("ok"):
        print(data.get("error", "Save failed."))
        return
    print(f"Saved: {data['path']}")


if __name__ == "__main__":
    main()
