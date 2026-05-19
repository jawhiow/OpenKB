"""Tests for openkb/workflows/auto_review_overlap.py."""
from __future__ import annotations

import json
from pathlib import Path

from openkb.workflows.auto_review_overlap import evaluate_overlap, overlap_config


def _make_kb(tmp_path: Path) -> Path:
    kb_dir = tmp_path / "kb"
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / "wiki" / "companies").mkdir(parents=True)
    (kb_dir / ".openkb").mkdir(parents=True)
    return kb_dir


def test_overlap_zero_when_no_existing_pages(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    result = evaluate_overlap("AI 投资研究 HBM 资本开支", kb_dir)
    assert result.max_overlap == 0.0
    assert result.top_hits == []
    assert result.scanned_pages == 0


def test_overlap_detects_high_similarity(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "concepts" / "HBM.md").write_text(
        "# HBM\n\nHBM 是高带宽存储, 用于 AI 算力和 GPU。资本开支与产能周期是核心。",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "concepts" / "光纤通信.md").write_text(
        "# 光纤通信\n\n光纤通信涉及光模块、CPO 与硅光技术, 数据中心需求驱动。",
        encoding="utf-8",
    )
    summary = "本报告分析 HBM 高带宽存储, AI 算力, GPU 资本开支与产能周期。"
    result = evaluate_overlap(summary, kb_dir)
    assert result.scanned_pages == 2
    assert result.max_overlap > 0
    assert result.top_hits
    assert result.top_hits[0].page == "concepts/HBM"


def test_overlap_cache_persisted(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "concepts" / "HBM.md").write_text("HBM 是高带宽存储, AI 算力", encoding="utf-8")
    evaluate_overlap("HBM AI 算力", kb_dir)
    cache_path = kb_dir / ".openkb" / "concept_terms_cache.json"
    assert cache_path.exists()
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["version"] == 1
    assert "concepts/HBM.md" in cache["entries"]
    assert "terms" in cache["entries"]["concepts/HBM.md"]


def test_overlap_min_score_filters_top_hits(tmp_path: Path):
    kb_dir = _make_kb(tmp_path)
    (kb_dir / "wiki" / "concepts" / "未相关.md").write_text(
        "完全不同的主题与术语集合用于测试低相似度", encoding="utf-8"
    )
    summary = "AI 半导体 GPU 投资分析"
    result = evaluate_overlap(summary, kb_dir, min_score=0.9)
    assert result.top_hits == []  # filtered out by high min_score
    # max_overlap still reflects actual similarity
    assert 0.0 <= result.max_overlap < 0.9


def test_overlap_config_defaults_and_overrides():
    cfg = overlap_config(None)
    assert cfg["reject_threshold"] == 0.80
    assert cfg["hold_threshold"] == 0.60

    cfg2 = overlap_config({
        "auto_review": {
            "overlap": {
                "reject_threshold": 0.85,
                "hold_threshold": 0.50,
                "top_k": 3,
            }
        }
    })
    assert cfg2["reject_threshold"] == 0.85
    assert cfg2["hold_threshold"] == 0.50
    assert cfg2["top_k"] == 3
