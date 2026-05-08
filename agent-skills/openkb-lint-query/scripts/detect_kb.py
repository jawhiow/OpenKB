from __future__ import annotations

import argparse
from pathlib import Path

from _runtime import (
    config_language,
    emit_json,
    git_status,
    markdown_pages,
    package_available,
    read_text,
    resolve_kb,
    simple_config,
    tool_available,
    wiki_root,
)


def build_detection(cwd: str) -> dict:
    kb_root, warnings = resolve_kb(cwd)
    if kb_root is None:
        return {
            "found": False,
            "cwd": str(Path(cwd).resolve()),
            "warnings": warnings,
            "openkb_command": tool_available("openkb"),
            "openkb_package": package_available("openkb"),
            "qmd_command": tool_available("qmd"),
        }

    wiki = wiki_root(kb_root)
    config = simple_config(kb_root / ".openkb" / "config.yaml")
    counts: dict[str, int] = {"index": 1 if (wiki / "index.md").exists() else 0}
    for name in ("summaries", "companies", "industries", "concepts", "explorations", "reports"):
        directory = wiki / name
        counts[name] = len(list(directory.glob("*.md"))) if directory.exists() else 0

    agents_path = wiki / "AGENTS.md"
    agents_excerpt = read_text(agents_path)[:1600] if agents_path.exists() else ""
    git = git_status(kb_root)

    return {
        "found": True,
        "kb_root": str(kb_root),
        "wiki_root": str(wiki),
        "language": config_language(kb_root),
        "config_keys": sorted(config.keys()),
        "openkb_command": tool_available("openkb"),
        "openkb_package": package_available("openkb"),
        "qmd_command": tool_available("qmd"),
        "has_evidence_map": (wiki / "evidence_map.json").exists(),
        "has_agents_md": agents_path.exists(),
        "agents_md_excerpt": agents_excerpt,
        "is_git_repo": bool(git.get("is_git_repo")),
        "git_status_short": git.get("status", ""),
        "counts": counts,
        "markdown_pages": len(markdown_pages(wiki, content_only=False)),
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect an OpenKB runtime knowledge base.")
    parser.add_argument("--cwd", default=".", help="Directory inside or near an OpenKB knowledge base.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    data = build_detection(args.cwd)
    if args.json:
        emit_json(data)
        return

    if not data["found"]:
        print("No OpenKB knowledge base found.")
        for warning in data.get("warnings", []):
            print(f"- {warning}")
        return
    print(f"KB root: {data['kb_root']}")
    print(f"Wiki root: {data['wiki_root']}")
    print(f"Language: {data['language']}")
    print(f"OpenKB command: {data['openkb_command']}")
    print(f"OpenKB package: {data['openkb_package']}")
    print(f"qmd command: {data['qmd_command']}")
    print(f"Evidence map: {data['has_evidence_map']}")
    print(f"Git repo: {data['is_git_repo']}")


if __name__ == "__main__":
    main()
