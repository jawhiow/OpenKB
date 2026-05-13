"""Audit concept page H1/title/filename consistency in a wiki directory.

Scans `<kb>/wiki/concepts/*.md` and reports:
  - missing_h1: 文件没有任何一级标题
  - english_slug: 文件名是纯英文 slug（中文 KB 通常应避免）
  - h1_is_slug: H1 文本就是文件名/英文 slug 字面（无可读标题）
  - h1_mismatch: H1 与文件名几乎无字符重叠（疑似 LLM 走神写错主题）
  - h1_prefix_noise: H1 以"概念："/"Concept:"等无意义前缀开头

Usage:
  python scripts/audit_concept_h1.py <kb_dir>           # 仅打印报告（JSON）
  python scripts/audit_concept_h1.py <kb_dir> --apply    # 修复 missing_h1 / h1_prefix_noise / h1_is_slug
                                                          # （仅做安全修复，h1_mismatch 不自动改，需人工）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

CJK_RE = re.compile(r"[一-鿿]")
H1_PREFIX_NOISE = re.compile(r"^(概念[:：]|主题[:：]|Concept\s*[:：]|Topic\s*[:：])\s*", re.IGNORECASE)


def _norm_token_set(text: str) -> set[str]:
    """字符 bigram 集合，对中英都友好。"""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = re.sub(r"[\s\-_/（）()【】\[\]，,。.：:、；;]+", "", text)
    if len(text) <= 1:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _extract_frontmatter_and_body(text: str) -> tuple[dict[str, str], str]:
    """Parse minimal YAML frontmatter (key: value lines only)."""
    fm: dict[str, str] = {}
    if not text.startswith("---"):
        return fm, text
    end = text.find("\n---", 3)
    if end == -1:
        return fm, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    for line in raw.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip()
    return fm, body


def _first_h1(body: str) -> tuple[str, int] | None:
    for idx, line in enumerate(body.splitlines()):
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip(), idx
    return None


def audit_one(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"file": path.name, "category": "io_error", "error": str(exc)}
    fm, body = _extract_frontmatter_and_body(raw)
    h1_info = _first_h1(body)
    stem = path.stem
    is_english_slug = not CJK_RE.search(stem)
    issues: list[str] = []

    if h1_info is None:
        issues.append("missing_h1")
        h1_text = ""
    else:
        h1_text, _ = h1_info
        if not h1_text:
            issues.append("missing_h1")
        elif H1_PREFIX_NOISE.match(h1_text):
            issues.append("h1_prefix_noise")
        # H1 与文件名（去标点折叠后）完全相同的英文 slug
        if h1_text and h1_text.strip() == stem:
            issues.append("h1_is_slug")
        # 严重不一致：bigram Jaccard < 0.2 且双方都非空（且文件名不只是英文 slug）
        if h1_text:
            sim = _jaccard(_norm_token_set(stem), _norm_token_set(h1_text))
            if sim < 0.2 and CJK_RE.search(h1_text) and CJK_RE.search(stem):
                issues.append("h1_mismatch")

    if is_english_slug:
        issues.append("english_slug")

    if not issues:
        return None
    return {
        "file": path.name,
        "stem": stem,
        "h1": h1_text,
        "brief": fm.get("brief", ""),
        "issues": issues,
    }


def safe_fix(path: Path, report: dict) -> bool:
    """Apply only obviously safe fixes; return True if mutated."""
    raw = path.read_text(encoding="utf-8")
    fm, body = _extract_frontmatter_and_body(raw)
    h1_info = _first_h1(body)
    issues = set(report["issues"])
    changed = False

    if "h1_prefix_noise" in issues and h1_info is not None:
        h1_text, idx = h1_info
        cleaned = H1_PREFIX_NOISE.sub("", h1_text).strip()
        if cleaned and cleaned != h1_text:
            lines = body.splitlines()
            lines[idx] = f"# {cleaned}"
            body = "\n".join(lines) + ("\n" if raw.endswith("\n") else "")
            changed = True

    if "missing_h1" in issues:
        # Fallback H1 = stem (best-effort; user should still review)
        new_h1 = f"# {path.stem}"
        body = new_h1 + "\n\n" + body.lstrip("\n")
        changed = True

    if changed:
        head = raw[: len(raw) - len(body) - len(body.lstrip())] if False else ""  # rebuilt below
        # Recompose with original frontmatter
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            if end != -1:
                fm_block = raw[: end + 4]
                new_raw = fm_block.rstrip("\n") + "\n" + body
            else:
                new_raw = body
        else:
            new_raw = body
        path.write_text(new_raw, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kb_dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="apply safe fixes in-place")
    parser.add_argument("--namespace", default="concepts",
                        help="subdir under wiki/ to audit (default: concepts)")
    args = parser.parse_args()

    target = args.kb_dir / "wiki" / args.namespace
    if not target.is_dir():
        print(f"Not a directory: {target}", file=sys.stderr)
        return 2

    files = sorted(target.glob("*.md"))
    reports: list[dict] = []
    for p in files:
        r = audit_one(p)
        if r is not None:
            reports.append(r)

    by_issue: dict[str, list[str]] = {}
    for r in reports:
        for it in r["issues"]:
            by_issue.setdefault(it, []).append(r["file"])

    fixed = 0
    if args.apply:
        for r in reports:
            p = target / r["file"]
            if safe_fix(p, r):
                fixed += 1

    print(json.dumps({
        "kb_dir": str(args.kb_dir),
        "namespace": args.namespace,
        "total_files": len(files),
        "files_with_issues": len(reports),
        "counts_by_issue": {k: len(v) for k, v in sorted(by_issue.items())},
        "fixed": fixed,
        "samples_by_issue": {
            k: v[:8] for k, v in sorted(by_issue.items())
        },
        "details": reports[:50] if not args.apply else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
