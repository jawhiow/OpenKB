"""Tests for openkb/workflows/auto_review_signals.py."""
from __future__ import annotations

from openkb.workflows.auto_review_signals import (
    apply_hard_signal_caps,
    detect_hard_signals,
    hard_signals_config,
)


def test_detect_no_signals_for_neutral_filename():
    signals = detect_hard_signals("国海证券-公司深度研究-2026.pdf")
    assert not signals.any_match
    assert signals.durability_cap is None
    assert signals.research_depth_cap is None


def test_detect_ephemeral_caps_durability():
    signals = detect_hard_signals("投资避雷针-20260515.pdf")
    assert "避雷" in signals.ephemeral_matches
    assert signals.durability_cap == 3
    assert signals.research_depth_cap is None


def test_detect_transcript_caps_research_depth():
    signals = detect_hard_signals("对话汽车-硬实力乘势共振-录音纪要.pdf")
    assert signals.transcript_matches  # 对话/纪要/录音 all match
    assert signals.research_depth_cap == 5
    assert signals.durability_cap is None


def test_detect_news_caps_both():
    signals = detect_hard_signals("AI快讯-2026.txt")
    assert "快讯" in signals.news_matches
    assert signals.durability_cap == 3
    assert signals.research_depth_cap == 5


def test_config_overrides_default_patterns():
    config = {
        "auto_review": {
            "hard_signals": {
                "ephemeral_patterns": ["特定模式"],
                "transcript_patterns": [],
                "news_patterns": [],
            }
        }
    }
    patterns = hard_signals_config(config)
    assert patterns["ephemeral_patterns"] == ("特定模式",)
    signals = detect_hard_signals("一个 特定模式 报告", config=config)
    assert "特定模式" in signals.ephemeral_matches
    assert signals.durability_cap == 3
    # default ephemeral patterns no longer apply
    signals2 = detect_hard_signals("每日点评", config=config)
    assert "每日" not in signals2.ephemeral_matches


def test_apply_caps_lowers_score_and_recomputes_total():
    signals = detect_hard_signals("每日避雷针-2026.pdf")
    assert signals.durability_cap == 3
    scorecard = {
        "version": "v2",
        "total_score": 78,
        "dimensions": {
            "research_depth": {"score": 12, "max": 15, "reason": ""},
            "durability": {"score": 12, "max": 15, "reason": "high"},
            "source_coverage": {"score": 14, "max": 15, "reason": ""},
            "factual_density": {"score": 13, "max": 15, "reason": ""},
            "retrieval_value": {"score": 9, "max": 10, "reason": ""},
            "novelty_vs_kb": {"score": 7, "max": 10, "reason": ""},
            "structure_clarity": {"score": 4, "max": 5, "reason": ""},
            "actionability": {"score": 3, "max": 5, "reason": ""},
            "cross_linking": {"score": 2, "max": 5, "reason": ""},
            "topic_fit": {"score": 2, "max": 5, "reason": ""},
        },
    }
    result = apply_hard_signal_caps(scorecard, signals)
    assert result["dimensions"]["durability"]["score"] == 3
    assert result["dimensions"]["durability"]["raw_score"] == 12
    # total recomputed
    expected_total = 12 + 3 + 14 + 13 + 9 + 7 + 4 + 3 + 2 + 2
    assert result["total_score"] == expected_total
    # research_depth untouched (no cap from ephemeral-only signal)
    assert result["dimensions"]["research_depth"]["score"] == 12
    assert "raw_score" not in result["dimensions"]["research_depth"]


def test_apply_caps_noop_when_within_cap():
    signals = detect_hard_signals("每日避雷针-2026.pdf")
    scorecard = {
        "version": "v2",
        "total_score": 60,
        "dimensions": {
            "durability": {"score": 2, "max": 15, "reason": ""},
        },
    }
    result = apply_hard_signal_caps(scorecard, signals)
    assert result["dimensions"]["durability"]["score"] == 2
    assert "raw_score" not in result["dimensions"]["durability"]
