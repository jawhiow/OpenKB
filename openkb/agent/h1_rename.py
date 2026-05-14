"""LLM-assisted resolution for `h1_mismatch` and `h1_is_english_slug` issues.

`openkb compact --fix-h1` only handles safe failure modes (missing H1, prefix
noise). The two risky modes — H1 drifted from filename, or filename is an
English slug while content is Chinese — require semantic judgement. This module
asks an LLM to recommend one of four actions per page, then applies the
recommended ones in place.

Actions:
  * ``rewrite_h1``  — stem is canonical; the H1 drifted. Replace H1 with
    the LLM's ``target_h1``.
  * ``rename_file`` — H1 is canonical; the stem is wrong (e.g. English slug
    or overly long). Move the file to ``target_stem`` and rewrite every
    ``[[concepts/old]]`` / ``[X](concepts/old.md)`` cross-reference.
  * ``split``       — the page conflates two concepts. Suggestion only;
    not executed.
  * ``manual``      — LLM couldn't decide. Suggestion only; not executed.

Pipeline:

    suggestions = propose_h1_renames(kb_dir, model=...)  # LLM, dry-run
    result = apply_h1_renames(kb_dir, suggestions)        # destructive

This module is deliberately scoped to ``concepts/`` only (companies/ and
industries/ have stricter naming contracts and are left alone).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openkb.llm_runtime import completion
from openkb.lint import iter_h1_violations

# Reuse the wikilink rewriter from concept_merge.
from openkb.concept_merge import _rewrite_concept_refs

CONCEPTS_NAMESPACE = "concepts"

MAX_BODY_CHARS = 2000
DEFAULT_AUTO_APPLY_CONFIDENCE = 0.7

VALID_ACTIONS = ("rewrite_h1", "rename_file", "split", "manual")

_CJK_RE = re.compile(r"[一-鿿]")
_SLUG_SAFE_RE = re.compile(r"[^\w\-一-鿿]+")
_H1_RE = re.compile(r"^# (?!#).*$", re.MULTILINE)


_LLM_SYSTEM_PROMPT = """\
You are OpenKB's concept-page filename rectifier. You inspect a single concept
page where the H1 title and the markdown filename (stem) disagree, and decide
how to repair the page so its slug, title, and content all describe the same
canonical concept.

Canonical concept names in this KB:
- A short Chinese noun phrase (typically ≤ 12 CJK chars, never exceed 30).
- No English slugs (e.g. ``AIoT_monetization``), no parenthetical glosses
  baked into the slug, no quarterly figures, no event verbs.
- The H1 may include a parenthetical English gloss (e.g. ``# 护城河（Economic Moat）``);
  the stem must remain the bare Chinese noun.

Return JSON only, no markdown fences.
"""

_LLM_USER_TEMPLATE = """\
Inspect this concept page and choose ONE repair action.

[stem (filename without .md)]
{stem}

[current H1]
{h1}

[brief from frontmatter]
{brief}

[body excerpt — first {body_chars} chars]
{body}

Actions (pick exactly one):

1. ``rewrite_h1`` — the stem IS the canonical concept noun; the H1 drifted to
   a different topic. Return ``target_h1`` as a short Chinese title; optionally
   add a parenthetical English gloss. ``target_h1`` MUST share most chars with
   the stem.

2. ``rename_file`` — the H1 IS the canonical concept noun; the stem is wrong
   (English slug, overly long descriptive phrase, or unrelated). Return
   ``target_stem`` as the canonical filename: a short Chinese noun, ≤ 16 chars,
   only Chinese characters, ASCII letters, digits, ``-``, or ``_``. No spaces,
   no punctuation, no parentheses, no English gloss. The new stem MUST share
   most chars with the H1 (minus any parenthetical gloss).

3. ``split`` — the page mixes two distinct reusable concepts and cannot be
   reduced to one canonical name. Return ``split_concepts`` as an array of
   exactly two objects ``{{"name": str, "title": str, "summary": str}}``.
   This action is advisory; OpenKB will NOT execute the split automatically.

4. ``manual`` — the page is too ambiguous to repair from this excerpt alone
   (e.g. body is empty, contains only links, or describes neither the stem
   nor the H1). Return ``rationale`` explaining what a human needs to check.

Return JSON with this exact shape (omit fields that don't apply):
{{
  "action": "rewrite_h1" | "rename_file" | "split" | "manual",
  "target_h1": "...",
  "target_stem": "...",
  "split_concepts": [
    {{"name": "...", "title": "...", "summary": "..."}}
  ],
  "rationale": "one short sentence in {language}",
  "confidence": 0.0
}}

`confidence` is your self-rated certainty in [0, 1]. Use < 0.7 if the body is
sparse or ambiguous.

Return ONLY valid JSON, no fences, no explanation.
"""


@dataclass
class H1RenameSuggestion:
    """A single LLM recommendation for one concept page."""

    stem: str
    path: str  # relative to wiki/, e.g. "concepts/护城河.md"
    h1: str
    brief: str
    violation_kinds: list[str]
    action: str  # one of VALID_ACTIONS
    target_h1: str = ""
    target_stem: str = ""
    split_concepts: list[dict[str, str]] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0
    auto_applicable: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Inspection helpers
# ---------------------------------------------------------------------------


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---"):
        return "", raw
    end = raw.find("\n---", 3)
    if end == -1:
        return "", raw
    return raw[: end + 4], raw[end + 4:].lstrip("\n")


def _extract_brief(frontmatter: str) -> str:
    for line in frontmatter.splitlines():
        line = line.strip()
        if line.startswith("brief:"):
            return line[len("brief:"):].strip()
    return ""


def _extract_h1(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip()
    return ""


def _collect_targets(kb_dir: Path) -> list[tuple[Path, list[str]]]:
    """Pick concept pages whose H1 issues require LLM judgement."""
    wiki = kb_dir / "wiki"
    out: list[tuple[Path, list[str]]] = []
    for path, kinds in iter_h1_violations(wiki, CONCEPTS_NAMESPACE):
        kinds_only = [k for k, _ in kinds]
        if any(k in {"h1_mismatch", "h1_is_english_slug"} for k in kinds_only):
            out.append((path, kinds_only))
    return out


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _build_messages(
    *,
    stem: str,
    h1: str,
    brief: str,
    body_excerpt: str,
    language: str,
) -> list[dict[str, str]]:
    user = _LLM_USER_TEMPLATE.format(
        stem=stem,
        h1=h1 or "(missing)",
        brief=brief or "(empty)",
        body=body_excerpt or "(empty)",
        body_chars=MAX_BODY_CHARS,
        language=language,
    )
    return [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _parse_llm_json(text: str) -> dict[str, Any]:
    """Robust-ish JSON parse: strip fences if the LLM ignored instructions."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # last resort: try to find the first {...} block
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"action": "manual", "rationale": "LLM returned non-JSON output."}


def _normalize_suggestion(
    raw: dict[str, Any],
    *,
    stem: str,
    h1: str,
    auto_apply_threshold: float,
) -> tuple[str, str, str, list[dict[str, str]], str, float, bool]:
    action = str(raw.get("action") or "").strip()
    if action not in VALID_ACTIONS:
        action = "manual"

    target_h1 = str(raw.get("target_h1") or "").strip() if action == "rewrite_h1" else ""
    target_stem = str(raw.get("target_stem") or "").strip() if action == "rename_file" else ""

    if action == "rename_file" and target_stem:
        # Sanitize: drop disallowed characters; cap length at 30.
        target_stem = _SLUG_SAFE_RE.sub("", target_stem).strip("-_")[:30]
        if not target_stem or target_stem == stem:
            action = "manual"
            target_stem = ""

    if action == "rewrite_h1" and not target_h1:
        action = "manual"

    if action == "rewrite_h1" and target_h1 == h1:
        action = "manual"
        target_h1 = ""

    split_concepts: list[dict[str, str]] = []
    if action == "split":
        raw_list = raw.get("split_concepts") or []
        if isinstance(raw_list, list):
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                title = str(item.get("title") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if name and title:
                    split_concepts.append({"name": name, "title": title, "summary": summary})
        if len(split_concepts) < 2:
            action = "manual"

    rationale = str(raw.get("rationale") or "").strip()
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    auto_applicable = (
        action in {"rewrite_h1", "rename_file"} and confidence >= auto_apply_threshold
    )

    return action, target_h1, target_stem, split_concepts, rationale, confidence, auto_applicable


# ---------------------------------------------------------------------------
# Public API — propose
# ---------------------------------------------------------------------------


def propose_h1_renames(
    kb_dir: Path,
    *,
    model: str,
    language: str = "Chinese",
    auto_apply_threshold: float = DEFAULT_AUTO_APPLY_CONFIDENCE,
    on_progress: Any = None,
) -> list[H1RenameSuggestion]:
    """Ask the LLM how to repair each H1-mismatched concept page.

    Pure analysis — does not touch disk. The caller decides which suggestions
    to apply via :func:`apply_h1_renames`.
    """
    targets = _collect_targets(kb_dir)
    suggestions: list[H1RenameSuggestion] = []
    wiki = kb_dir / "wiki"

    for index, (path, kinds) in enumerate(targets, start=1):
        relative = str(path.relative_to(wiki)).replace("\\", "/")
        if callable(on_progress):
            on_progress(f"[{index}/{len(targets)}] {relative}")

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            suggestions.append(
                H1RenameSuggestion(
                    stem=path.stem,
                    path=relative,
                    h1="",
                    brief="",
                    violation_kinds=kinds,
                    action="manual",
                    rationale=f"Unreadable file: {exc}",
                    error=str(exc),
                )
            )
            continue

        frontmatter, body = _split_frontmatter(raw_text)
        h1 = _extract_h1(body)
        brief = _extract_brief(frontmatter)
        body_excerpt = body[:MAX_BODY_CHARS]

        try:
            response = completion(
                model=model,
                messages=_build_messages(
                    stem=path.stem,
                    h1=h1,
                    brief=brief,
                    body_excerpt=body_excerpt,
                    language=language,
                ),
            )
            raw_response = getattr(response, "text", "") or ""
            parsed = _parse_llm_json(raw_response)
            (
                action,
                target_h1,
                target_stem,
                split_concepts,
                rationale,
                confidence,
                auto_applicable,
            ) = _normalize_suggestion(
                parsed, stem=path.stem, h1=h1, auto_apply_threshold=auto_apply_threshold
            )
            suggestions.append(
                H1RenameSuggestion(
                    stem=path.stem,
                    path=relative,
                    h1=h1,
                    brief=brief,
                    violation_kinds=kinds,
                    action=action,
                    target_h1=target_h1,
                    target_stem=target_stem,
                    split_concepts=split_concepts,
                    rationale=rationale,
                    confidence=confidence,
                    auto_applicable=auto_applicable,
                )
            )
        except Exception as exc:  # noqa: BLE001 — surface LLM/runtime errors
            suggestions.append(
                H1RenameSuggestion(
                    stem=path.stem,
                    path=relative,
                    h1=h1,
                    brief=brief,
                    violation_kinds=kinds,
                    action="manual",
                    rationale=f"LLM call failed: {exc}",
                    error=str(exc),
                )
            )

    return suggestions


# ---------------------------------------------------------------------------
# Public API — apply
# ---------------------------------------------------------------------------


def _replace_first_h1(body: str, new_title: str) -> tuple[str, bool]:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            lines[i] = f"# {new_title.strip()}"
            return ("\n".join(lines) + ("\n" if body.endswith("\n") else "")), True
    # No H1 yet — prepend one.
    new_body = f"# {new_title.strip()}\n\n" + body
    return new_body, True


def _rewrite_one_file(path: Path, mapping: dict[str, str]) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return False
    updated = _rewrite_concept_refs(original, mapping)
    if updated == original:
        return False
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def apply_h1_renames(
    kb_dir: Path,
    suggestions: Iterable[H1RenameSuggestion | dict[str, Any]],
) -> dict[str, Any]:
    """Apply the user-approved subset of suggestions.

    Only ``rewrite_h1`` and ``rename_file`` are executable. ``split`` and
    ``manual`` are recorded as skipped.

    Returns a structured result the API/UI can render.
    """
    wiki = kb_dir / "wiki"
    concepts_dir = wiki / CONCEPTS_NAMESPACE

    h1_rewritten: list[dict[str, str]] = []
    renamed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    rename_map: dict[str, str] = {}

    for raw in suggestions:
        suggestion = raw if isinstance(raw, H1RenameSuggestion) else _from_dict(raw)
        if suggestion is None:
            continue

        if suggestion.action == "rewrite_h1":
            if not suggestion.target_h1:
                skipped.append({"path": suggestion.path, "reason": "missing target_h1"})
                continue
            path = concepts_dir / f"{suggestion.stem}.md"
            if not path.exists():
                errors.append({"path": suggestion.path, "reason": "file not found"})
                continue
            try:
                raw_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append({"path": suggestion.path, "reason": str(exc)})
                continue
            frontmatter, body = _split_frontmatter(raw_text)
            new_body, changed = _replace_first_h1(body, suggestion.target_h1)
            if not changed:
                skipped.append({"path": suggestion.path, "reason": "no H1 change"})
                continue
            new_raw = (frontmatter + "\n" + new_body) if frontmatter else new_body
            try:
                path.write_text(new_raw, encoding="utf-8")
                h1_rewritten.append(
                    {
                        "path": suggestion.path,
                        "old_h1": suggestion.h1,
                        "new_h1": suggestion.target_h1,
                    }
                )
            except OSError as exc:
                errors.append({"path": suggestion.path, "reason": str(exc)})

        elif suggestion.action == "rename_file":
            if not suggestion.target_stem:
                skipped.append({"path": suggestion.path, "reason": "missing target_stem"})
                continue
            old_path = concepts_dir / f"{suggestion.stem}.md"
            new_path = concepts_dir / f"{suggestion.target_stem}.md"
            if not old_path.exists():
                errors.append({"path": suggestion.path, "reason": "file not found"})
                continue
            if new_path.exists():
                errors.append(
                    {
                        "path": suggestion.path,
                        "reason": f"target {suggestion.target_stem}.md already exists",
                    }
                )
                continue
            try:
                old_path.rename(new_path)
                rename_map[suggestion.stem] = suggestion.target_stem
                renamed.append(
                    {
                        "path": suggestion.path,
                        "old_stem": suggestion.stem,
                        "new_stem": suggestion.target_stem,
                    }
                )
            except OSError as exc:
                errors.append({"path": suggestion.path, "reason": str(exc)})

        else:
            skipped.append({"path": suggestion.path, "reason": f"non-executable action: {suggestion.action}"})

    files_rewritten = 0
    if rename_map:
        for md_file in sorted(wiki.rglob("*.md")):
            if _rewrite_one_file(md_file, rename_map):
                files_rewritten += 1

    return {
        "h1_rewritten": h1_rewritten,
        "renamed": renamed,
        "skipped": skipped,
        "errors": errors,
        "files_rewritten": files_rewritten,
        "rename_map": rename_map,
    }


def _from_dict(payload: dict[str, Any]) -> H1RenameSuggestion | None:
    try:
        return H1RenameSuggestion(
            stem=str(payload.get("stem") or ""),
            path=str(payload.get("path") or ""),
            h1=str(payload.get("h1") or ""),
            brief=str(payload.get("brief") or ""),
            violation_kinds=list(payload.get("violation_kinds") or []),
            action=str(payload.get("action") or "manual"),
            target_h1=str(payload.get("target_h1") or ""),
            target_stem=str(payload.get("target_stem") or ""),
            split_concepts=list(payload.get("split_concepts") or []),
            rationale=str(payload.get("rationale") or ""),
            confidence=float(payload.get("confidence") or 0.0),
            auto_applicable=bool(payload.get("auto_applicable") or False),
            error=str(payload.get("error") or ""),
        )
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_suggestions_report(suggestions: list[H1RenameSuggestion]) -> str:
    """Render suggestions as a Markdown report (for compact reports/)."""
    if not suggestions:
        return "## H1 rename suggestions\n\n- (none)\n"

    lines: list[str] = ["## H1 rename suggestions", ""]
    grouped: dict[str, list[H1RenameSuggestion]] = {a: [] for a in VALID_ACTIONS}
    for s in suggestions:
        grouped.setdefault(s.action, []).append(s)

    for action in VALID_ACTIONS:
        items = grouped.get(action) or []
        lines.append(f"### {action} ({len(items)})")
        if not items:
            lines.append("- (none)")
            lines.append("")
            continue
        for s in items:
            arrow = ""
            if action == "rewrite_h1":
                arrow = f"  H1: `{s.h1}` → `{s.target_h1}`"
            elif action == "rename_file":
                arrow = f"  stem: `{s.stem}` → `{s.target_stem}`"
            elif action == "split":
                names = [c.get("name", "") for c in s.split_concepts]
                arrow = f"  split → {names}"
            lines.append(f"- `{s.path}` (conf={s.confidence:.2f})")
            if arrow:
                lines.append(arrow)
            if s.rationale:
                lines.append(f"  reason: {s.rationale}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
