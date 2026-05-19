"""Auto-review hard signals: filename-pattern detectors for ephemeral / transcript / news content.

These signals are deterministic and complement the LLM scorecard. They impose
hard caps on the ``durability`` and ``research_depth`` dimensions and feed the
auto-review decision engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


_DEFAULT_EPHEMERAL_PATTERNS: tuple[str, ...] = (
    "每日", "周报", "月报", "避雷", "收市", "盘前", "盘后",
    "晨会", "晨报", "早评", "夜报", "日报", "周观察",
)

_DEFAULT_TRANSCRIPT_PATTERNS: tuple[str, ...] = (
    "对话", "纪要", "路演", "电话会", "调研纪要", "访谈",
    "圆桌", "录音", "会议纪要",
)

_DEFAULT_NEWS_PATTERNS: tuple[str, ...] = (
    "快讯", "速报", "热点", "事件点评", "突发", "公告点评",
)

_EPHEMERAL_DURABILITY_CAP = 3
_TRANSCRIPT_RESEARCH_DEPTH_CAP = 5
_NEWS_BOTH_DURABILITY_CAP = 3
_NEWS_BOTH_RESEARCH_DEPTH_CAP = 5


@dataclass
class HardSignals:
    """Result of hard-signal detection for one document."""

    ephemeral_matches: list[str] = field(default_factory=list)
    transcript_matches: list[str] = field(default_factory=list)
    news_matches: list[str] = field(default_factory=list)
    durability_cap: int | None = None
    research_depth_cap: int | None = None

    @property
    def any_match(self) -> bool:
        return bool(self.ephemeral_matches or self.transcript_matches or self.news_matches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ephemeral_matches": list(self.ephemeral_matches),
            "transcript_matches": list(self.transcript_matches),
            "news_matches": list(self.news_matches),
            "durability_cap": self.durability_cap,
            "research_depth_cap": self.research_depth_cap,
            "any_match": self.any_match,
        }


def _normalized_patterns(value: Any, fallback: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        cleaned = tuple(str(item).strip() for item in value if str(item).strip())
        if cleaned:
            return cleaned
    return tuple(fallback)


def hard_signals_config(config: Mapping[str, Any] | None) -> dict[str, tuple[str, ...]]:
    """Return the effective pattern lists from an auto_review config block."""
    auto_review = {}
    if isinstance(config, Mapping):
        block = config.get("auto_review")
        if isinstance(block, Mapping):
            inner = block.get("hard_signals")
            if isinstance(inner, Mapping):
                auto_review = inner
    return {
        "ephemeral_patterns": _normalized_patterns(
            auto_review.get("ephemeral_patterns"), _DEFAULT_EPHEMERAL_PATTERNS
        ),
        "transcript_patterns": _normalized_patterns(
            auto_review.get("transcript_patterns"), _DEFAULT_TRANSCRIPT_PATTERNS
        ),
        "news_patterns": _normalized_patterns(
            auto_review.get("news_patterns"), _DEFAULT_NEWS_PATTERNS
        ),
    }


def detect_hard_signals(
    name: str,
    *,
    config: Mapping[str, Any] | None = None,
    patterns: Mapping[str, Iterable[str]] | None = None,
) -> HardSignals:
    """Inspect a document name for ephemeral/transcript/news patterns.

    Returns a :class:`HardSignals` with matched tokens and the dimension caps
    they impose. The caps follow the rubric in
    ``openkb/workflows/summary_pipeline.py``: ephemeral → durability ≤ 3,
    transcript → research_depth ≤ 5, news → both caps active.
    """
    effective = (
        {
            "ephemeral_patterns": tuple(patterns.get("ephemeral_patterns", ())),
            "transcript_patterns": tuple(patterns.get("transcript_patterns", ())),
            "news_patterns": tuple(patterns.get("news_patterns", ())),
        }
        if patterns is not None
        else hard_signals_config(config)
    )

    haystack = str(name or "")
    ephemeral = [token for token in effective["ephemeral_patterns"] if token and token in haystack]
    transcript = [token for token in effective["transcript_patterns"] if token and token in haystack]
    news = [token for token in effective["news_patterns"] if token and token in haystack]

    caps: list[int | None] = []
    research_caps: list[int | None] = []
    if ephemeral:
        caps.append(_EPHEMERAL_DURABILITY_CAP)
    if news:
        caps.append(_NEWS_BOTH_DURABILITY_CAP)
        research_caps.append(_NEWS_BOTH_RESEARCH_DEPTH_CAP)
    if transcript:
        research_caps.append(_TRANSCRIPT_RESEARCH_DEPTH_CAP)

    durability_cap = min(caps) if caps else None
    research_depth_cap = min(research_caps) if research_caps else None

    return HardSignals(
        ephemeral_matches=ephemeral,
        transcript_matches=transcript,
        news_matches=news,
        durability_cap=durability_cap,
        research_depth_cap=research_depth_cap,
    )


def apply_hard_signal_caps(scorecard: Mapping[str, Any], signals: HardSignals) -> dict[str, Any]:
    """Return a new scorecard with hard caps applied to the v2 dimensions.

    The original LLM-reported scores are preserved on each dimension under
    ``raw_score`` so the auto-review log can show why a dimension was capped.
    The ``total_score`` is recomputed from the post-cap dimension scores.
    """
    if not isinstance(scorecard, Mapping):
        return dict(scorecard) if isinstance(scorecard, Mapping) else {}

    dimensions = scorecard.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return dict(scorecard)

    new_dims: dict[str, dict[str, Any]] = {}
    changed = False
    for key, value in dimensions.items():
        if not isinstance(value, Mapping):
            new_dims[key] = dict(value) if isinstance(value, Mapping) else value
            continue
        item = dict(value)
        try:
            current = int(item.get("score", 0))
        except (TypeError, ValueError):
            current = 0
        cap: int | None = None
        cap_reason = ""
        if key == "durability" and signals.durability_cap is not None:
            cap = signals.durability_cap
            cap_reason = "hard_signal_cap:durability"
        elif key == "research_depth" and signals.research_depth_cap is not None:
            cap = signals.research_depth_cap
            cap_reason = "hard_signal_cap:research_depth"
        if cap is not None and current > cap:
            item["raw_score"] = current
            item["score"] = cap
            existing_reason = str(item.get("reason") or "").strip()
            note = f"[{cap_reason}] capped from {current} to {cap}"
            item["reason"] = f"{existing_reason} {note}".strip()
            changed = True
        new_dims[key] = item

    result = dict(scorecard)
    result["dimensions"] = new_dims
    if changed:
        result["total_score"] = sum(
            int(d.get("score", 0)) for d in new_dims.values() if isinstance(d, Mapping)
        )
    return result
