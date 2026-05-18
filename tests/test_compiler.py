"""Tests for openkb.agent.compiler pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from openkb.agent.compiler import (
    DEFAULT_COMPILE_CONCURRENCY,
    _COMPANIES_PLAN_USER,
    _CONCEPTS_PLAN_USER,
    _INVESTMENT_PAGES_PLAN_USER,
    compile_long_doc,
    compile_local_long_doc,
    compile_short_doc,
    compile_progress_callback,
    _build_pageindex_financial_evidence_pack,
    _compile_concepts,
    _dedupe_concept_plan,
    _ensure_summary_links_in_plan,
    _normalize_concept_links,
    _normalize_wiki_links,
    _registered_entity_link_aliases,
    _parse_json,
    _sanitize_concept_name,
    _extract_company_candidates_from_summary,
    _extract_concept_candidates_from_summary,
    _write_summary,
    _write_concept,
    _write_company,
    cleanup_generated_pages_for_source,
    _update_index,
    _read_wiki_context,
    _read_concept_briefs,
    _read_company_briefs,
    _add_related_link,
    _backlink_summary,
    _backlink_concepts,
    _canonicalize_company_item,
    _canonicalize_concept_item,
    _canonicalize_investment_page_item,
    _resolve_company_items_against_registry,
    _resolve_investment_page_plan_against_registry,
    get_compile_max_concurrency,
    _llm_call,
)
from openkb.entity_registry import EntityRegistry
from openkb.lint import find_broken_links
from openkb.llm_runtime import CompletionResult


def test_compile_max_concurrency_defaults_to_conservative_value(monkeypatch):
    monkeypatch.delenv("OPENKB_COMPILE_MAX_CONCURRENCY", raising=False)

    assert DEFAULT_COMPILE_CONCURRENCY == 2
    assert get_compile_max_concurrency() == 2


def test_compile_max_concurrency_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("OPENKB_COMPILE_MAX_CONCURRENCY", "1")
    assert get_compile_max_concurrency() == 1

    monkeypatch.setenv("OPENKB_COMPILE_MAX_CONCURRENCY", "bad")
    assert get_compile_max_concurrency() == DEFAULT_COMPILE_CONCURRENCY

    monkeypatch.setenv("OPENKB_COMPILE_MAX_CONCURRENCY", "0")
    assert get_compile_max_concurrency() == 1


def test_generated_page_plan_prompts_request_chinese_filenames():
    prompts = "\n".join([
        _COMPANIES_PLAN_USER,
        _INVESTMENT_PAGES_PLAN_USER,
        _CONCEPTS_PLAN_USER,
    ])

    assert "Chinese page filename" in prompts
    assert "Do not use English slugs" in prompts
    assert "must be an actual company" in _COMPANIES_PLAN_USER
    assert "must be a real industry" in _INVESTMENT_PAGES_PLAN_USER
    assert "If uncertain" in _COMPANIES_PLAN_USER
    assert "If uncertain" in _INVESTMENT_PAGES_PLAN_USER
    assert '"themes"' not in _INVESTMENT_PAGES_PLAN_USER
    assert '"metrics"' not in _INVESTMENT_PAGES_PLAN_USER
    assert '"risks"' not in _INVESTMENT_PAGES_PLAN_USER
    assert "themes, risks, metrics" in _CONCEPTS_PLAN_USER
    assert "suffix variant" in _CONCEPTS_PLAN_USER


def test_generated_page_items_prefer_chinese_title_for_filename():
    assert _canonicalize_company_item(
        {"name": "tsmc", "title": "台积电", "action": "create"}
    )["name"] == "台积电"
    assert _canonicalize_investment_page_item(
        {"name": "cloud-capex", "title": "云资本开支", "action": "create"}
    )["name"] == "云资本开支"
    assert _canonicalize_concept_item(
        {"name": "advanced-packaging", "title": "先进封装"}
    )["name"] == "先进封装"


def test_resolve_company_items_against_registry_uses_canonical_id(tmp_path):
    registry_dir = tmp_path / ".openkb" / "entity_registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "companies.yaml").write_text(
        (
            "companies:\n"
            "  tencent-holdings:\n"
            "    canonical_name: 腾讯控股有限公司\n"
            "    display_name: 腾讯控股\n"
            "    aliases: [腾讯控股, 腾讯控股有限公司, Tencent Holdings]\n"
        ),
        encoding="utf-8",
    )
    (registry_dir / "industries.yaml").write_text("industries: {}\n", encoding="utf-8")
    registry = EntityRegistry.load(tmp_path)

    items, resolved = _resolve_company_items_against_registry(
        [{"name": "腾讯控股有限公司", "title": "腾讯控股有限公司", "action": "create"}],
        registry,
    )

    assert items == [{"name": "tencent-holdings", "title": "腾讯控股有限公司", "action": "update"}]
    assert resolved[0].path == "companies/tencent-holdings"


def test_resolve_investment_page_plan_against_registry_canonicalizes_industry_and_drops_company(tmp_path):
    registry_dir = tmp_path / ".openkb" / "entity_registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "companies.yaml").write_text(
        (
            "companies:\n"
            "  tencent-holdings:\n"
            "    canonical_name: 腾讯控股有限公司\n"
            "    display_name: 腾讯控股\n"
            "    aliases: [腾讯控股]\n"
        ),
        encoding="utf-8",
    )
    (registry_dir / "industries.yaml").write_text(
        (
            "industries:\n"
            "  online-advertising:\n"
            "    canonical_name: 在线广告\n"
            "    display_name: 在线广告\n"
            "    aliases: [在线广告, 互联网广告]\n"
        ),
        encoding="utf-8",
    )
    registry = EntityRegistry.load(tmp_path)

    plan, resolved = _resolve_investment_page_plan_against_registry(
        {
            "industries": [
                {"name": "腾讯控股", "title": "腾讯控股", "action": "create"},
                {"name": "互联网广告", "title": "互联网广告", "action": "create"},
                {"name": "游戏行业", "title": "游戏行业", "action": "create"},
            ],
        },
        registry,
    )

    assert plan["industries"] == [
        {"name": "online-advertising", "title": "在线广告", "action": "update"},
        {"name": "游戏行业", "title": "游戏行业", "action": "create"},
    ]
    assert resolved[0].path == "industries/online-advertising"


def test_registered_entity_link_aliases_normalize_company_and_industry_links(tmp_path):
    registry_dir = tmp_path / ".openkb" / "entity_registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "companies.yaml").write_text(
        (
            "companies:\n"
            "  tencent-holdings:\n"
            "    canonical_name: 腾讯控股有限公司\n"
            "    display_name: 腾讯控股\n"
            "    aliases: [腾讯控股]\n"
        ),
        encoding="utf-8",
    )
    (registry_dir / "industries.yaml").write_text(
        (
            "industries:\n"
            "  online-advertising:\n"
            "    canonical_name: 在线广告\n"
            "    display_name: 在线广告\n"
            "    aliases: [互联网广告]\n"
        ),
        encoding="utf-8",
    )
    registry = EntityRegistry.load(tmp_path)

    aliases = _registered_entity_link_aliases(registry)
    text = "[[companies/腾讯控股]] benefits from [[industries/互联网广告]]."
    normalized = _normalize_wiki_links(
        text,
        aliases,
        allowed_slugs=set(),
        valid_pages={"companies/tencent-holdings", "industries/online-advertising"},
    )

    assert "[[companies/tencent-holdings]]" in normalized
    assert "[[industries/online-advertising]]" in normalized


def test_dedupe_concept_plan_merges_suffix_variant_into_existing_concept(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "价值投资理论来源.md").write_text(
        "# 价值投资理论来源\n\n既有内容。",
        encoding="utf-8",
    )

    deduped = _dedupe_concept_plan(
        wiki,
        {
            "create": [
                {
                    "name": "价值投资理论来源--85-格雷厄姆-15-费雪框架",
                    "title": "价值投资理论来源--85%格雷厄姆-15%费雪框架",
                }
            ],
            "update": [],
            "related": [],
        },
    )

    assert deduped["create"] == []
    assert deduped["update"] == [{"name": "价值投资理论来源", "title": "价值投资理论来源"}]
    assert deduped["related"] == []


def test_dedupe_concept_plan_keeps_distinct_english_compounds(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "attention.md").write_text(
        "# Attention\n\nExisting concept.",
        encoding="utf-8",
    )

    deduped = _dedupe_concept_plan(
        wiki,
        {
            "create": [
                {
                    "name": "flash-attention",
                    "title": "Flash Attention",
                }
            ],
            "update": [],
            "related": [],
        },
    )

    assert deduped["create"] == [{"name": "flash-attention", "title": "Flash Attention"}]
    assert deduped["update"] == []
    assert deduped["related"] == []


def test_llm_calls_emit_progress_events_for_job_details():
    events: list[str] = []

    with patch(
        "openkb.agent.compiler.completion",
        return_value=CompletionResult(text="ok", usage=None),
    ):
        with compile_progress_callback(events.append):
            result = _llm_call("gpt-4o-mini", [{"role": "user", "content": "Ping"}], "summary")

    assert result == "ok"
    assert events[0] == "LLM start: summary"
    assert events[-1].startswith("LLM done: summary")


class TestParseJson:
    def test_plain_json(self):
        assert _parse_json('[{"name": "foo"}]') == [{"name": "foo"}]

    def test_fenced_json(self):
        text = '```json\n[{"name": "bar"}]\n```'
        assert _parse_json(text) == [{"name": "bar"}]

    def test_invalid_json(self):
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json("not json")


class TestParseConceptsPlan:
    def test_dict_format(self):
        text = json.dumps({
            "create": [{"name": "foo", "title": "Foo"}],
            "update": [{"name": "bar", "title": "Bar"}],
            "related": ["baz"],
        })
        parsed = _parse_json(text)
        assert isinstance(parsed, dict)
        assert len(parsed["create"]) == 1
        assert len(parsed["update"]) == 1
        assert parsed["related"] == ["baz"]

    def test_fallback_list_format(self):
        text = json.dumps([{"name": "foo", "title": "Foo"}])
        parsed = _parse_json(text)
        assert isinstance(parsed, list)

    def test_fenced_dict(self):
        text = '```json\n{"create": [], "update": [], "related": []}\n```'
        parsed = _parse_json(text)
        assert isinstance(parsed, dict)
        assert parsed["create"] == []


def test_pageindex_financial_evidence_pack_selects_source_json_pages(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "sources" / "annual-report.json").write_text(
        json.dumps([
            {"page": 1, "content": "Corporate profile only."},
            {
                "page": 4,
                "content": (
                    "Revenue increased while gross profit and gross margin improved. "
                    "Dividend and share repurchase plans are disclosed."
                ),
            },
        ]),
        encoding="utf-8",
    )

    pack = _build_pageindex_financial_evidence_pack(
        wiki,
        "annual-report",
        "Annual report summary",
    )

    assert "annual/financial report synthesis" in pack
    assert "p.4: Revenue increased" in pack
    assert "p.1:" not in pack
    assert "Do not write placeholders" in pack


class TestConceptLinkNormalization:
    def test_bare_unknown_wiki_links_are_unlinked(self):
        aliases = {
            "ai半导体投资框架": "AI半导体投资框架",
        }
        text = (
            "[[台积电]] benefits from [[AI半导体投资框架]] and "
            "[[summaries/report]]."
        )

        normalized = _normalize_wiki_links(
            text,
            aliases,
            {"AI半导体投资框架"},
            {"summaries/report", "concepts/AI半导体投资框架"},
        )

        assert "[[台积电]]" not in normalized
        assert "台积电 benefits" in normalized
        assert "[[concepts/AI半导体投资框架]]" in normalized
        assert "[[summaries/report]]" in normalized

    def test_summary_links_are_added_to_plan_when_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        plan = {"create": [], "update": [], "related": []}
        summary = "AI depends on [[concepts/HBM高带宽内存]] and [[concepts/CoWoS先进封装]]."

        updated = _ensure_summary_links_in_plan(wiki, summary, plan)

        created = {item["name"]: item["title"] for item in updated["create"]}
        assert created == {
            "HBM高带宽内存": "HBM高带宽内存",
            "CoWoS先进封装": "CoWoS先进封装",
        }

    def test_normalizes_known_aliases_and_unlinks_unknown_concepts(self):
        text = (
            "See [[concepts/AI半导体]], [[concepts/HBM高带宽内存]], "
            "and [[summaries/report]]."
        )
        aliases = {"AI半导体": "ai-semiconductors"}

        normalized = _normalize_concept_links(
            text,
            aliases,
            allowed_slugs={"ai-semiconductors"},
        )

        assert "[[concepts/ai-semiconductors]]" in normalized
        assert "HBM高带宽内存" in normalized
        assert "[[concepts/HBM高带宽内存]]" not in normalized
        assert "[[summaries/report]]" in normalized


    def test_normalizes_misnamespaced_and_bare_namespace_references(self):
        text = (
            "See [[concepts/themes-Corporate-Governance]], "
            "concepts/AI-risk, and risks/capital-controls."
        )

        normalized = _normalize_wiki_links(
            text,
            aliases={},
            allowed_slugs=set(),
            valid_pages=set(),
        )

        assert "[[concepts/themes-Corporate-Governance]]" not in normalized
        assert "concepts/themes-Corporate-Governance" not in normalized
        assert "concepts/AI-risk" not in normalized
        assert "risks/capital-controls" not in normalized
        assert "Corporate-Governance" in normalized
        assert "AI-risk" in normalized
        assert "capital-controls" in normalized


class TestParseBriefContent:
    def test_dict_with_brief_and_content(self):
        text = json.dumps({"brief": "A short desc", "content": "# Full page\n\nDetails."})
        parsed = _parse_json(text)
        assert parsed["brief"] == "A short desc"
        assert "# Full page" in parsed["content"]

    def test_plain_text_fallback(self):
        """If LLM returns plain text, _parse_json raises — caller handles fallback."""
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json("Just plain markdown text without JSON")


class TestSanitizeConceptName:
    def test_ascii_passthrough(self):
        assert _sanitize_concept_name("hello-world") == "hello-world"

    def test_spaces_replaced(self):
        assert _sanitize_concept_name("hello world") == "hello-world"

    def test_chinese(self):
        result = _sanitize_concept_name("注意力机制")
        assert result == "注意力机制"

    def test_japanese(self):
        result = _sanitize_concept_name("トランスフォーマー")
        assert result == "トランスフォーマー"

    def test_french_accents(self):
        result = _sanitize_concept_name("réseau neuronal")
        assert "r" in result
        assert result != "r-seau-neuronal"  # accented chars preserved, not stripped

    def test_distinct_chinese_names_no_collision(self):
        a = _sanitize_concept_name("注意力机制")
        b = _sanitize_concept_name("变压器模型")
        assert a != b

    def test_empty_fallback(self):
        assert _sanitize_concept_name("!!!") == "unnamed-concept"

    def test_nfkc_normalization(self):
        # U+FF21 (fullwidth A) should normalize to regular A
        assert _sanitize_concept_name("\uff21\uff22") == "AB"


class TestWriteSummary:
    def test_writes_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "# Summary\n\nContent here.")
        path = wiki / "summaries" / "my-doc.md"
        assert path.exists()
        text = path.read_text()
        assert "doc_type: short" in text
        assert "full_text: sources/my-doc.md" in text
        assert "# Summary" in text

    def test_writes_without_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "# Summary\n\nContent here.")
        path = wiki / "summaries" / "my-doc.md"
        text = path.read_text()
        assert "doc_type: short" in text
        assert "full_text: sources/my-doc.md" in text

    def test_writes_summary_page_references_to_evidence_map(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(
            wiki,
            "report",
            "# Report\n\n## Source Evidence\n- p.3: HBM supply is a bottleneck.",
            doc_type="local-long",
        )

        evidence = json.loads((wiki / "evidence_map.json").read_text(encoding="utf-8"))
        assert evidence["summaries/report.md"][0]["source"] == "sources/report.json"
        assert evidence["summaries/report.md"][0]["summary"] == "summaries/report"
        assert evidence["summaries/report.md"][0]["page"] == "3"
        assert "HBM supply is a bottleneck" in evidence["summaries/report.md"][0]["snippet"]


class TestWriteConcept:
    def test_new_concept_with_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False, brief="Mechanism for selective focus")
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [paper.pdf]" in text
        assert "brief: Mechanism for selective focus" in text
        assert "# Attention" in text

    def test_new_concept_without_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False)
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [paper.pdf]" in text
        assert "brief:" not in text

    def test_update_concept_updates_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\nbrief: Old brief\n---\n\n# Attention\n\nOld content.",
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New info.", "paper2.pdf", True, brief="Updated brief")
        text = (concepts / "attention.md").read_text()
        assert "paper2.pdf" in text
        assert "paper1.pdf" in text
        assert "brief: Updated brief" in text
        assert "Old brief" not in text

    def test_update_concept_appends_source(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nOld content.",
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New info from paper2.", "paper2.pdf", True)
        text = (concepts / "attention.md").read_text()
        assert "paper2.pdf" in text
        assert "paper1.pdf" in text
        assert "New info from paper2." in text

    def test_multisource_update_preserves_existing_concept_evidence(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "value-investing.md").write_text(
            "---\n"
            "sources: [summaries/book-a.md]\n"
            "brief: Invest with a margin of safety\n"
            "---\n\n"
            "# Value Investing\n\n"
            "Book A frames value investing around intrinsic value.\n\n"
            "## Source Evidence\n"
            "- [[summaries/book-a]] p.12: Intrinsic value anchors the process.\n",
            encoding="utf-8",
        )

        _write_concept(
            wiki,
            "value-investing",
            (
                "# Value Investing\n\n"
                "Book B emphasizes owner earnings and durable moats.\n\n"
                "## Source Evidence\n"
                "- [[summaries/book-b]] p.8: Owner earnings matter.\n"
            ),
            "summaries/book-b.md",
            True,
            brief="Owner earnings and durable moats",
        )

        text = (concepts / "value-investing.md").read_text(encoding="utf-8")
        assert "summaries/book-a.md" in text
        assert "summaries/book-b.md" in text
        assert "brief: Owner earnings and durable moats" in text
        assert "Book A frames value investing around intrinsic value" in text
        assert "Book B emphasizes owner earnings and durable moats" in text
        assert "## Source Update: [[summaries/book-a]]" in text
        assert "## Source Update: [[summaries/book-b]]" in text


class TestWriteCompany:
    def test_new_company_with_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_company(
            wiki,
            "TSMC",
            "# TSMC\n\nAI foundry exposure.",
            "summaries/report.md",
            False,
            brief="AI foundry bellwether",
        )
        path = wiki / "companies" / "TSMC.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [summaries/report.md]" in text
        assert "brief: AI foundry bellwether" in text
        assert "# TSMC" in text

    def test_multisource_update_preserves_existing_company_evidence(self, tmp_path):
        wiki = tmp_path / "wiki"
        companies = wiki / "companies"
        companies.mkdir(parents=True)
        (companies / "Tencent.md").write_text(
            "---\n"
            "sources: [summaries/tencent-2025.md]\n"
            "brief: 2025 growth and cash generation\n"
            "---\n\n"
            "# Tencent\n\n"
            "2025 revenue was RMB 751.8 billion.\n\n"
            "## Source Evidence\n"
            "- [[summaries/tencent-2025]] p.4: Revenue was RMB 751.8 billion.\n",
            encoding="utf-8",
        )

        _write_company(
            wiki,
            "Tencent",
            (
                "# Tencent\n\n"
                "2024 revenue was RMB 660.3 billion.\n\n"
                "## Source Evidence\n"
                "- [[summaries/tencent-2024]] p.4: Revenue was RMB 660.3 billion.\n"
            ),
            "summaries/tencent-2024.md",
            True,
            brief="2024 steady growth",
        )

        text = (companies / "Tencent.md").read_text(encoding="utf-8")
        assert "summaries/tencent-2025.md" in text
        assert "summaries/tencent-2024.md" in text
        assert "brief: 2025 growth and cash generation" in text
        assert "2025 revenue was RMB 751.8 billion" in text
        assert "2024 revenue was RMB 660.3 billion" in text
        assert "## Source Update: [[summaries/tencent-2025]]" in text
        assert "## Source Update: [[summaries/tencent-2024]]" in text


class TestUpdateIndex:
    def test_appends_entries_with_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention", "transformer"],
                       doc_brief="Introduces transformers",
                       concept_briefs={"attention": "Focus mechanism", "transformer": "NN architecture"})
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]] (short) - Introduces transformers" in text
        assert "[[concepts/attention]] - Focus mechanism" in text
        assert "[[concepts/transformer]] - NN architecture" in text

    def test_appends_company_entries_with_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Companies\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(
            wiki,
            "report",
            [],
            doc_brief="AI semiconductor report",
            company_names=["TSMC"],
            company_briefs={"TSMC": "AI foundry bellwether"},
        )
        text = (wiki / "index.md").read_text()
        assert "[[summaries/report]] (short) - AI semiconductor report" in text
        assert "[[companies/TSMC]] - AI foundry bellwether" in text

    def test_appends_industry_entries_and_ignores_legacy_dedicated_page_kwargs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )

        _update_index(
            wiki,
            "report",
            [],
            doc_brief="AI semiconductor report",
            industry_names=["semiconductors"],
            industry_briefs={"semiconductors": "Semiconductor value-chain structure"},
            theme_names=["ai-capex"],
            theme_briefs={"ai-capex": "Cloud AI spending cycle"},
            metric_names=["hbm-supply"],
            metric_briefs={"hbm-supply": "Capacity indicator for AI memory"},
            risk_names=["export-controls"],
            risk_briefs={"export-controls": "Policy constraint on AI chips"},
        )

        text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "## Industries" in text
        assert "## Themes" not in text
        assert "## Metrics" not in text
        assert "## Risks" not in text
        assert "[[industries/semiconductors]] - Semiconductor value-chain structure" in text
        assert "[[themes/ai-capex]]" not in text
        assert "[[metrics/hbm-supply]]" not in text
        assert "[[risks/export-controls]]" not in text

    def test_updates_only_exact_concept_row(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n"
            "- [[concepts/transformer]] - Uses [[concepts/attention]] internally\n"
            "- [[concepts/attention]] - Old brief\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(
            wiki,
            "my-doc",
            ["attention"],
            concept_briefs={"attention": "New brief"},
        )
        text = (wiki / "index.md").read_text()
        assert "- [[concepts/transformer]] - Uses [[concepts/attention]] internally" in text
        assert "- [[concepts/attention]] - New brief" in text
        assert text.count("[[concepts/attention]] - New brief") == 1

    def test_no_duplicates(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/my-doc]] - Old brief\n\n## Concepts\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", [], doc_brief="New brief")
        text = (wiki / "index.md").read_text()
        assert text.count("[[summaries/my-doc]]") == 1

    def test_backwards_compat_no_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention"])
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]]" in text
        assert "[[concepts/attention]]" in text

    def test_updates_concept_brief_only_inside_concepts_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n"
            "- [[summaries/my-doc]] (short) - Mentions [[concepts/attention]] here\n\n"
            "## Concepts\n"
            "- [[concepts/attention]] - Old brief\n\n"
            "## Explorations\n",
            encoding="utf-8",
        )

        _update_index(
            wiki,
            "my-doc",
            ["attention"],
            concept_briefs={"attention": "New brief"},
        )

        text = (wiki / "index.md").read_text()
        assert "- [[summaries/my-doc]] (short) - Mentions [[concepts/attention]] here" in text
        assert "- [[concepts/attention]] - New brief" in text
        assert "- [[concepts/attention]] - Old brief" not in text

    def test_adds_concept_entry_when_link_exists_outside_concepts_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n"
            "- [[summaries/my-doc]] (short) - Mentions [[concepts/attention]] here\n\n"
            "## Concepts\n\n"
            "## Explorations\n",
            encoding="utf-8",
        )

        _update_index(
            wiki,
            "my-doc",
            ["attention"],
            concept_briefs={"attention": "New brief"},
        )

        text = (wiki / "index.md").read_text()
        assert "- [[summaries/my-doc]] (short) - Mentions [[concepts/attention]] here" in text
        assert "- [[concepts/attention]] - New brief" in text


class TestReadWikiContext:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        index, concepts = _read_wiki_context(wiki)
        assert index == ""
        assert concepts == []

    def test_with_content(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
        concepts_dir = wiki / "concepts"
        concepts_dir.mkdir()
        (concepts_dir / "attention.md").write_text("# Attention", encoding="utf-8")
        (concepts_dir / "transformer.md").write_text("# Transformer", encoding="utf-8")
        index, concepts = _read_wiki_context(wiki)
        assert "# Index" in index
        assert concepts == ["attention", "transformer"]


class TestReadConceptBriefs:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "concepts").mkdir()
        assert _read_concept_briefs(wiki) == "(none yet)"

    def test_no_concepts_dir(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        assert _read_concept_briefs(wiki) == "(none yet)"

    def test_reads_briefs_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\nAttention is a mechanism that allows models to focus on relevant parts.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- attention:" in result
        assert "Attention is a mechanism" in result
        assert "sources" not in result
        assert "---" not in result

    def test_reads_briefs_without_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "transformer.md").write_text(
            "Transformer is a neural network architecture based on attention.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- transformer:" in result
        assert "Transformer is a neural network" in result

    def test_truncates_long_content(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        long_body = "A" * 300
        (concepts / "longconcept.md").write_text(long_body, encoding="utf-8")
        result = _read_concept_briefs(wiki)
        # The brief part should be truncated at 150 chars
        brief = result.split("- longconcept: ", 1)[1]
        assert len(brief) == 150
        assert brief == "A" * 150

    def test_sorted_alphabetically(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "zebra.md").write_text("Zebra concept.", encoding="utf-8")
        (concepts / "apple.md").write_text("Apple concept.", encoding="utf-8")
        (concepts / "mango.md").write_text("Mango concept.", encoding="utf-8")
        result = _read_concept_briefs(wiki)
        lines = result.strip().splitlines()
        slugs = [line.split(":")[0].lstrip("- ") for line in lines]
        assert slugs == ["apple", "mango", "zebra"]

    def test_reads_brief_from_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\nbrief: Selective focus mechanism\n---\n\n# Attention\n\nLong content...",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- attention: Selective focus mechanism" in result

    def test_falls_back_to_body_truncation(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "old.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\nOld concept without brief field.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- old: Old concept without brief field." in result


class TestReadCompanyBriefs:
    def test_reads_company_briefs_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        companies = wiki / "companies"
        companies.mkdir(parents=True)
        (companies / "TSMC.md").write_text(
            "---\nsources: [report.md]\nbrief: AI foundry bellwether\n---\n\n# TSMC",
            encoding="utf-8",
        )

        result = _read_company_briefs(wiki)

        assert "- TSMC: AI foundry bellwether" in result


class TestCleanupGeneratedPagesForSource:
    def test_removes_single_source_pages_and_index_entries(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "companies").mkdir(parents=True)
        (wiki / "industries").mkdir(parents=True)
        (wiki / "themes").mkdir(parents=True)
        (wiki / "metrics").mkdir(parents=True)
        (wiki / "risks").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n- [[summaries/report]] (local-long) - Report\n\n"
            "## Companies\n- [[companies/TSMC]] - Old company\n\n"
            "## Industries\n- [[industries/semiconductors]] - Old industry\n\n"
            "## Themes\n- [[themes/ai-capex]] - Old theme\n\n"
            "## Metrics\n- [[metrics/hbm-supply]] - Old metric\n\n"
            "## Risks\n- [[risks/export-controls]] - Old risk\n\n"
            "## Concepts\n"
            "- [[concepts/HBM]] - Old concept\n"
            "- [[concepts/Shared]] - Shared concept\n\n",
            encoding="utf-8",
        )
        (wiki / "concepts" / "HBM.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n# HBM",
            encoding="utf-8",
        )
        (wiki / "companies" / "TSMC.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n# TSMC",
            encoding="utf-8",
        )
        (wiki / "industries" / "semiconductors.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n# Semiconductors",
            encoding="utf-8",
        )
        (wiki / "themes" / "ai-capex.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n# AI CAPEX",
            encoding="utf-8",
        )
        (wiki / "metrics" / "hbm-supply.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n# HBM Supply",
            encoding="utf-8",
        )
        (wiki / "risks" / "export-controls.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n# Export Controls",
            encoding="utf-8",
        )
        (wiki / "concepts" / "Shared.md").write_text(
            "---\nsources: [summaries/other.md, summaries/report.md]\n---\n\n"
            "# Shared\n\nSee concepts/stale-path.\n\n"
            "## Source Evidence\n"
            "- [[summaries/report]]: TODO: add exact supporting claims and page references.\n",
            encoding="utf-8",
        )

        removed = cleanup_generated_pages_for_source(wiki, "report")

        assert removed == [
            "companies/TSMC",
            "concepts/HBM",
            "industries/semiconductors",
            "metrics/hbm-supply",
            "risks/export-controls",
            "themes/ai-capex",
        ]
        assert not (wiki / "concepts" / "HBM.md").exists()
        assert not (wiki / "companies" / "TSMC.md").exists()
        assert not (wiki / "industries" / "semiconductors.md").exists()
        assert not (wiki / "themes" / "ai-capex.md").exists()
        assert not (wiki / "metrics" / "hbm-supply.md").exists()
        assert not (wiki / "risks" / "export-controls.md").exists()
        assert (wiki / "concepts" / "Shared.md").exists()
        shared_text = (wiki / "concepts" / "Shared.md").read_text(encoding="utf-8")
        assert "TODO" not in shared_text
        assert "add exact supporting claims" not in shared_text
        assert "concepts/stale-path" not in shared_text
        assert "stale-path" in shared_text
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[companies/TSMC]]" not in index_text
        assert "[[concepts/HBM]]" not in index_text
        assert "[[industries/semiconductors]]" not in index_text
        assert "[[themes/ai-capex]]" not in index_text
        assert "[[metrics/hbm-supply]]" not in index_text
        assert "[[risks/export-controls]]" not in index_text
        assert "[[concepts/Shared]]" in index_text


class TestCompanyFallbackExtraction:
    def test_extracts_companies_from_investment_summary_lines(self):
        summary = (
            "- **首选股**（Overweight）：台积电（Top Pick）、世芯（Alchip）、"
            "创意（GUC）、FOCI。\n"
            "- **主要受益者**：京元电（KYEC）。"
        )

        companies = _extract_company_candidates_from_summary(summary, max_companies=5)

        assert companies == [
            {"name": "台积电", "title": "台积电", "action": "create"},
            {"name": "Alchip", "title": "世芯 (Alchip)", "action": "create"},
            {"name": "GUC", "title": "创意 (GUC)", "action": "create"},
            {"name": "FOCI", "title": "FOCI", "action": "create"},
            {"name": "KYEC", "title": "京元电 (KYEC)", "action": "create"},
        ]


class TestConceptFallbackExtraction:
    def test_extracts_durable_investment_concepts_from_summary_headings(self):
        summary = (
            "## 先进封装：CoWoS与SoIC——算力核心\n"
            "## AI ASIC：定制化浪潮\n"
            "## 存储：HBM、DDR4与NOR短缺\n"
            "## 中国AI芯片生态\n"
            "## 测试设备与耗材：大封装+CPO的受益者\n"
        )

        concepts = _extract_concept_candidates_from_summary(summary, max_concepts=8)
        names = [item["name"] for item in concepts]

        assert names == [
            "Advanced_Packaging",
            "CoWoS",
            "SoIC",
            "AI_ASIC",
            "HBM",
            "NOR_Flash",
            "China_AI_GPU",
            "Semiconductor_Testing",
        ]

    def test_extracts_macro_cpu_gpu_and_policy_concepts(self):
        summary = (
            "## 宏观需求与供应链动态\n云资本支出和CSP CAPEX继续扩张。\n"
            "## AI CPU与全球AI GPU\nNVIDIA Grace CPU、GB300和Rubin路线图。\n"
            "## 风险与监测\n出口管制、地缘政治与非AI半导体景气周期。"
        )

        concepts = _extract_concept_candidates_from_summary(summary, max_concepts=20)
        names = [item["name"] for item in concepts]

        assert "Cloud_CAPEX" in names
        assert "AI_CPU" in names
        assert "AI_GPU" in names
        assert "Export_Controls" in names
        assert "Semiconductor_Cycle" in names


class TestBacklinkSummary:
    def test_adds_missing_concept_links(self, tmp_path):
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\n# Summary\n\nContent about attention.",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        assert "[[concepts/attention]]" in text
        assert "[[concepts/transformer]]" in text

    def test_skips_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\n# Summary\n\nSee [[concepts/attention]].",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        # attention already linked, should not duplicate
        assert text.count("[[concepts/attention]]") == 1
        # transformer should be added
        assert "[[concepts/transformer]]" in text

    def test_no_op_when_all_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        original = "# Summary\n\n[[concepts/attention]] and [[concepts/transformer]]"
        (summaries / "paper.md").write_text(original, encoding="utf-8")
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        assert (summaries / "paper.md").read_text() == original

    def test_skips_if_file_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Should not raise
        _backlink_summary(wiki, "nonexistent", ["attention"])

    def test_merges_into_existing_section(self, tmp_path):
        """Second add should merge into existing ## Related Concepts, not duplicate."""
        wiki = tmp_path / "wiki"
        summaries = wiki / "summaries"
        summaries.mkdir(parents=True)
        (summaries / "paper.md").write_text(
            "# Summary\n\nContent.\n\n## Related Concepts\n- [[concepts/attention]]\n",
            encoding="utf-8",
        )
        _backlink_summary(wiki, "paper", ["attention", "transformer"])
        text = (summaries / "paper.md").read_text()
        assert text.count("## Related Concepts") == 1
        assert "[[concepts/transformer]]" in text
        assert text.count("[[concepts/attention]]") == 1


class TestBacklinkConcepts:
    def test_adds_summary_link_to_concept(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\n# Attention\n\nContent.",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert "[[summaries/paper]]" in text
        assert "## Related Documents" in text

    def test_skips_if_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "# Attention\n\nBased on [[summaries/paper]].",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert text.count("[[summaries/paper]]") == 1
        assert "## Related Documents" not in text

    def test_merges_into_existing_section(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "# Attention\n\n## Related Documents\n- [[summaries/old-paper]]\n",
            encoding="utf-8",
        )
        _backlink_concepts(wiki, "new-paper", ["attention"])
        text = (concepts / "attention.md").read_text()
        assert text.count("## Related Documents") == 1
        assert "[[summaries/old-paper]]" in text
        assert "[[summaries/new-paper]]" in text

    def test_skips_missing_concept_file(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "concepts").mkdir(parents=True)
        # Should not raise
        _backlink_concepts(wiki, "paper", ["nonexistent"])

    def test_cleans_existing_placeholder_artifacts_when_linking(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "# Attention\n\nSee concepts/old-path.\n\n"
            "## Source Evidence\n"
            "- [[summaries/old-paper]]: TODO: add exact supporting claims and page references.\n",
            encoding="utf-8",
        )

        _backlink_concepts(wiki, "paper", ["attention"])

        text = (concepts / "attention.md").read_text(encoding="utf-8")
        assert "TODO" not in text
        assert "add exact supporting claims" not in text
        assert "concepts/old-path" not in text
        assert "old-path" in text
        assert "[[summaries/paper]]" in text


class TestAddRelatedLink:
    def test_adds_see_also_link(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nSome content.",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper2.pdf")
        text = (concepts / "attention.md").read_text()
        assert "[[summaries/new-doc]]" in text
        assert "paper2.pdf" in text

    def test_skips_if_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nSee also: [[summaries/new-doc]]",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper1.pdf")
        text = (concepts / "attention.md").read_text()
        assert text.count("[[summaries/new-doc]]") == 1

    def test_skips_if_file_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Should not raise
        _add_related_link(wiki, "nonexistent", "doc", "file.pdf")

    def test_cleans_existing_placeholder_artifacts_when_adding_related_link(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n"
            "# Attention\n\nUse risks/old-risk as context.\n\n"
            "## Source Evidence\n"
            "- [[summaries/old-paper]]: TODO: add exact supporting claims and page references.\n",
            encoding="utf-8",
        )

        _add_related_link(wiki, "attention", "new-doc", "paper2.pdf")

        text = (concepts / "attention.md").read_text(encoding="utf-8")
        assert "TODO" not in text
        assert "add exact supporting claims" not in text
        assert "risks/old-risk" not in text
        assert "old-risk" in text
        assert "[[summaries/new-doc]]" in text


def _mock_completion(responses: list[str]):
    """Create a mock for compiler.completion that returns responses in order."""
    call_count = {"n": 0}

    def side_effect(*args, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        usage.prompt_tokens_details = None
        return CompletionResult(text=responses[idx], usage=usage)

    return side_effect


def _mock_acompletion(responses: list[str]):
    """Create an async mock for compiler.acompletion."""
    call_count = {"n": 0}

    async def side_effect(*args, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        usage.prompt_tokens_details = None
        return CompletionResult(text=responses[idx], usage=usage)

    return side_effect


class TestCompileShortDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        # Setup KB structure
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nSome content about transformers.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        summary_response = json.dumps({
            "brief": "Discusses transformers",
            "content": "# Summary\n\nThis document discusses transformers.",
        })
        concepts_list_response = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        concept_page_response = json.dumps({
            "brief": "NN architecture using self-attention",
            "content": "# Transformer\n\nA neural network architecture.",
        })

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([summary_response, concepts_list_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response]),
            ),
        ):
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Verify summary written
        summary_path = wiki / "summaries" / "test-doc.md"
        assert summary_path.exists()
        assert "full_text: sources/test-doc.md" in summary_path.read_text()

        # Verify concept written
        concept_path = wiki / "concepts" / "transformer.md"
        assert concept_path.exists()
        assert "sources: [summaries/test-doc.md]" in concept_path.read_text()

        # Verify index updated
        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/test-doc]]" in index_text
        assert "[[concepts/transformer]]" in index_text

    @pytest.mark.asyncio
    async def test_short_doc_syncs_generated_industry_pages_from_staging(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "industries").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Industries\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "industry-report.md"
        source_path.write_text("# Industry Report\n\nSemiconductor value chain.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "industry-report.pdf").write_bytes(b"fake")

        summary_response = json.dumps({
            "brief": "Semiconductor industry report",
            "content": "# Summary\n\nSemiconductor value-chain structure.",
        })
        company_plan_response = json.dumps({"companies": []})
        investment_page_plan_response = json.dumps({
            "industries": [
                {"name": "semiconductors", "title": "Semiconductors", "action": "create"},
            ],
            "themes": [],
            "metrics": [],
            "risks": [],
        })
        concept_plan_response = json.dumps({"create": [], "update": [], "related": []})
        industry_page_response = json.dumps({
            "brief": "Semiconductor value-chain structure",
            "content": "# Semiconductors\n\nLinked to [[summaries/industry-report]].",
        })

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    summary_response,
                    company_plan_response,
                    investment_page_plan_response,
                    concept_plan_response,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([industry_page_response]),
            ),
        ):
            await compile_short_doc("industry-report", source_path, tmp_path, "gpt-4o-mini")

        assert (wiki / "industries" / "semiconductors.md").exists()
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[industries/semiconductors]] - Semiconductor value-chain structure" in index_text

    @pytest.mark.asyncio
    async def test_rolls_back_summary_when_concept_planning_fails(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        original_index = "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n"
        (wiki / "index.md").write_text(original_index, encoding="utf-8")
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nSome content.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()

        summary_response = json.dumps({
            "brief": "Temporary summary",
            "content": "# Summary\n\nThis should not be committed if planning fails.",
        })
        usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        usage.prompt_tokens_details = None

        with patch(
            "openkb.agent.compiler.completion",
            side_effect=[
                CompletionResult(text=summary_response, usage=usage),
                RuntimeError("planner down"),
            ],
        ):
            with pytest.raises(RuntimeError, match="planner down"):
                await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        assert not (wiki / "summaries" / "test-doc.md").exists()
        assert not (wiki / "evidence_map.json").exists()
        assert (wiki / "index.md").read_text(encoding="utf-8") == original_index

    @pytest.mark.asyncio
    async def test_handles_bad_json(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "doc.md"
        source_path.write_text("Content", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()

        with patch(
            "openkb.agent.compiler.completion",
            side_effect=_mock_completion(["Plain summary text", "not valid json"]),
        ):
            # Should not raise
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary should still be written
        assert (wiki / "summaries" / "doc.md").exists()


class TestCompileLongDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        summary_path = wiki / "summaries" / "big-doc.md"
        summary_path.write_text("# Big Doc\n\nPageIndex summary tree.", encoding="utf-8")
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "big-doc.pdf").write_bytes(b"fake")

        overview_response = "Overview of the big document."
        concepts_list_response = json.dumps({
            "create": [{"name": "deep-learning", "title": "Deep Learning"}],
            "update": [],
            "related": [],
        })
        concept_page_response = json.dumps({
            "brief": "Subfield of ML using neural networks",
            "content": "# Deep Learning\n\nA subfield of ML with source evidence on p.1.",
        })

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([overview_response, concepts_list_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response]),
            ),
        ):
            await compile_long_doc(
                "big-doc", summary_path, "doc-123", tmp_path, "gpt-4o-mini"
            )

        concept_path = wiki / "concepts" / "deep-learning.md"
        assert concept_path.exists()
        assert "Deep Learning" in concept_path.read_text()

        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/big-doc]]" in index_text
        assert "[[concepts/deep-learning]]" in index_text


class TestCompileLocalLongDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline_from_page_json(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "report.json"
        source_path.write_text(
            json.dumps([
                {"page": 1, "content": "Top pick is TSMC.", "images": []},
                {"page": 2, "content": "HBM demand reaches 32bn Gb.", "images": []},
            ]),
            encoding="utf-8",
        )
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")

        summary_response = json.dumps({
            "brief": "Investment report on AI semiconductors",
            "content": "# Summary\n\nEvidence from p.1 and p.2 links [[concepts/HBM]].",
        })
        concepts_list_response = json.dumps({
            "create": [{"name": "hbm", "title": "HBM"}],
            "update": [],
            "related": [],
        })
        concept_page_response = json.dumps({
            "brief": "High-bandwidth memory used by AI accelerators",
            "content": "# HBM\n\nMemory bottleneck for AI accelerators.",
        })

        completion_side_effect = _mock_completion([summary_response, concepts_list_response])

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=completion_side_effect,
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response]),
            ),
        ):
            await compile_local_long_doc("report", source_path, tmp_path, "gpt-4o-mini")

        summary_text = (wiki / "summaries" / "report.md").read_text(encoding="utf-8")
        assert "doc_type: local-long" in summary_text
        assert "full_text: sources/report.json" in summary_text
        assert "[[concepts/hbm]]" in summary_text
        assert (wiki / "concepts" / "hbm.md").exists()


class TestCompileConceptsPlan:
    """Integration tests for _compile_concepts with the new plan format."""

    def _setup_wiki(self, tmp_path, existing_concepts=None):
        """Helper to set up a wiki directory with optional existing concepts."""
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "companies").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Companies\n\n## Concepts\n",
            encoding="utf-8",
        )
        (tmp_path / "raw").mkdir(exist_ok=True)
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        if existing_concepts:
            for name, content in existing_concepts.items():
                (wiki / "concepts" / f"{name}.md").write_text(
                    content, encoding="utf-8",
                )

        return wiki

    @pytest.mark.asyncio
    async def test_create_and_update_flow(self, tmp_path):
        """Pre-existing 'attention' concept; plan creates 'flash-attention' and updates 'attention'."""
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "attention": "---\nsources: [old-paper.pdf]\n---\n\n# Attention\n\nOriginal content about attention.",
        })

        plan_response = json.dumps({
            "create": [{"name": "flash-attention", "title": "Flash Attention"}],
            "update": [{"name": "attention", "title": "Attention"}],
            "related": [],
        })
        create_page_response = json.dumps({
            "brief": "Efficient attention algorithm",
            "content": "# Flash Attention\n\nAn efficient attention algorithm.",
        })
        update_page_response = json.dumps({
            "brief": "Updated attention mechanism",
            "content": "# Attention\n\nUpdated content with new info.",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document about attention mechanisms."}
        summary = "Summary of the document."

        call_order = {"n": 0}

        async def ordered_acompletion(*args, **kwargs):
            idx = call_order["n"]
            call_order["n"] += 1
            # create tasks come first, then update tasks
            if idx == 0:
                text = create_page_response
            else:
                text = update_page_response
            usage = MagicMock(prompt_tokens=100, completion_tokens=50)
            usage.prompt_tokens_details = None
            return CompletionResult(text=text, usage=usage)

        with (
            patch("openkb.agent.compiler.completion", side_effect=_mock_completion([plan_response])),
            patch("openkb.agent.compiler.acompletion", side_effect=ordered_acompletion),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5,
            )

        # Verify flash-attention created
        fa_path = wiki / "concepts" / "flash-attention.md"
        assert fa_path.exists()
        fa_text = fa_path.read_text()
        assert "sources: [summaries/test-doc.md]" in fa_text
        assert "Flash Attention" in fa_text

        # Verify attention updated (is_update=True path in _write_concept)
        att_path = wiki / "concepts" / "attention.md"
        assert att_path.exists()
        att_text = att_path.read_text()
        assert "summaries/test-doc.md" in att_text
        assert "old-paper.pdf" in att_text

        # Verify index updated
        index_text = (wiki / "index.md").read_text()
        assert "[[concepts/flash-attention]]" in index_text
        assert "[[concepts/attention]]" in index_text

    @pytest.mark.asyncio
    async def test_company_plan_creates_company_pages_and_index(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "test-doc.md").write_text(
            "TSMC benefits from [[companies/TSMC]] and [[concepts/HBM]].",
            encoding="utf-8",
        )

        company_plan_response = json.dumps({
            "companies": [
                {"name": "TSMC", "title": "TSMC", "action": "create"},
            ],
        })
        concept_plan_response = json.dumps({
            "create": [{"name": "HBM", "title": "HBM"}],
            "update": [],
            "related": [],
        })
        company_page_response = json.dumps({
            "brief": "AI foundry bellwether",
            "content": "# TSMC\n\nAI exposure via [[concepts/HBM]] and [[summaries/test-doc]].",
        })
        concept_page_response = json.dumps({
            "brief": "Memory bottleneck for AI accelerators",
            "content": "# HBM\n\nUsed by [[companies/TSMC]] customers.",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "TSMC benefits from [[companies/TSMC]] and [[concepts/HBM]]."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([company_plan_response, concept_plan_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response, concept_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert (wiki / "companies" / "TSMC.md").exists()
        assert (wiki / "concepts" / "HBM.md").exists()
        summary_text = (wiki / "summaries" / "test-doc.md").read_text(encoding="utf-8")
        assert "[[companies/TSMC]]" in summary_text
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[companies/TSMC]] - AI foundry bellwether" in index_text
        assert "[[concepts/HBM]] - Memory bottleneck for AI accelerators" in index_text

    @pytest.mark.asyncio
    async def test_company_plan_uses_registered_canonical_company_path(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "tencent-report.md").write_text(
            "腾讯控股有限公司游戏和广告业务更新。",
            encoding="utf-8",
        )
        registry_dir = tmp_path / ".openkb" / "entity_registry"
        registry_dir.mkdir(parents=True)
        (registry_dir / "companies.yaml").write_text(
            (
                "companies:\n"
                "  tencent-holdings:\n"
                "    canonical_name: 腾讯控股有限公司\n"
                "    display_name: 腾讯控股\n"
                "    aliases: [腾讯控股, 腾讯控股有限公司, Tencent Holdings]\n"
            ),
            encoding="utf-8",
        )
        (registry_dir / "industries.yaml").write_text("industries: {}\n", encoding="utf-8")

        company_plan_response = json.dumps({
            "companies": [
                {"name": "腾讯控股有限公司", "title": "腾讯控股有限公司", "action": "create"},
            ],
        })
        investment_page_plan_response = json.dumps({"industries": []})
        concept_plan_response = json.dumps({"create": [], "update": [], "related": []})
        company_page_response = json.dumps({
            "brief": "游戏和广告业务更新",
            "content": "# 腾讯控股有限公司\n\n公司业务更新。",
        })

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    company_plan_response,
                    investment_page_plan_response,
                    concept_plan_response,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                {"role": "system", "content": "You are a wiki agent."},
                {"role": "user", "content": "Document content."},
                "腾讯控股有限公司游戏和广告业务更新。",
                "tencent-report",
                5,
            )

        assert (wiki / "companies" / "tencent-holdings.md").exists()
        assert not (wiki / "companies" / "腾讯控股有限公司.md").exists()
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[companies/tencent-holdings]] - 游戏和广告业务更新" in index_text

    @pytest.mark.asyncio
    async def test_registered_company_summary_concept_link_does_not_create_concept(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "tencent-report.md").write_text(
            "See [[concepts/腾讯控股]] for details.",
            encoding="utf-8",
        )
        registry_dir = tmp_path / ".openkb" / "entity_registry"
        registry_dir.mkdir(parents=True)
        (registry_dir / "companies.yaml").write_text(
            (
                "companies:\n"
                "  tencent-holdings:\n"
                "    canonical_name: 腾讯控股有限公司\n"
                "    display_name: 腾讯控股\n"
                "    aliases: [腾讯控股]\n"
            ),
            encoding="utf-8",
        )
        (registry_dir / "industries.yaml").write_text("industries: {}\n", encoding="utf-8")

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    json.dumps({"companies": []}),
                    json.dumps({"industries": []}),
                    json.dumps({"create": [], "update": [], "related": []}),
                ]),
            ),
            patch("openkb.agent.compiler.acompletion", side_effect=_mock_acompletion([])) as mock_acompletion,
        ):
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                {"role": "system", "content": "You are a wiki agent."},
                {"role": "user", "content": "Document content."},
                "See [[concepts/腾讯控股]] for details.",
                "tencent-report",
                5,
            )

        assert mock_acompletion.await_count == 0
        assert not (wiki / "concepts" / "腾讯控股.md").exists()

    @pytest.mark.asyncio
    async def test_investment_page_plan_routes_only_industries_to_dedicated_pages(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "test-doc.md").write_text(
            "The report covers semiconductor industry structure, AI CAPEX, HBM supply, and export controls.",
            encoding="utf-8",
        )

        company_plan_response = json.dumps({"companies": []})
        investment_page_plan_response = json.dumps({
            "industries": [
                {"name": "semiconductors", "title": "Semiconductors", "action": "create"},
            ],
        })
        concept_plan_response = json.dumps({
            "create": [
                {"name": "ai-capex", "title": "AI CAPEX"},
                {"name": "hbm-supply", "title": "HBM Supply"},
                {"name": "export-controls", "title": "Export Controls"},
            ],
            "update": [],
            "related": [],
        })
        generated_page_responses = [
            json.dumps({
                "brief": "Semiconductor value-chain structure",
                "content": "# Semiconductors\n\nFoundry and memory supply chain linked to [[summaries/test-doc]].",
            }),
            json.dumps({
                "brief": "Cloud AI spending cycle",
                "content": "# AI CAPEX\n\nSpending theme linked to [[summaries/test-doc]].",
            }),
            json.dumps({
                "brief": "Capacity indicator for AI memory",
                "content": "# HBM Supply\n\nMetric linked to [[summaries/test-doc]].",
            }),
            json.dumps({
                "brief": "Policy constraint on AI chips",
                "content": "# Export Controls\n\nRisk linked to [[summaries/test-doc]].",
            }),
        ]

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "The report covers semiconductor industry structure, AI CAPEX, HBM supply, and export controls."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    company_plan_response,
                    investment_page_plan_response,
                    concept_plan_response,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion(generated_page_responses),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
        )

        assert (wiki / "industries" / "semiconductors.md").exists()
        assert (wiki / "concepts" / "ai-capex.md").exists()
        assert (wiki / "concepts" / "hbm-supply.md").exists()
        assert (wiki / "concepts" / "export-controls.md").exists()
        assert not (wiki / "themes").exists()
        assert not (wiki / "metrics").exists()
        assert not (wiki / "risks").exists()

        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[industries/semiconductors]] - Semiconductor value-chain structure" in index_text
        assert "[[concepts/ai-capex]] - Cloud AI spending cycle" in index_text
        assert "[[concepts/hbm-supply]] - Capacity indicator for AI memory" in index_text
        assert "[[concepts/export-controls]] - Policy constraint on AI chips" in index_text
        assert "## Themes" not in index_text
        assert "## Metrics" not in index_text
        assert "## Risks" not in index_text

    @pytest.mark.asyncio
    async def test_industry_plan_uses_registered_path_and_drops_company_alias(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "tencent-report.md").write_text(
            "腾讯控股受益于互联网广告复苏。",
            encoding="utf-8",
        )
        registry_dir = tmp_path / ".openkb" / "entity_registry"
        registry_dir.mkdir(parents=True)
        (registry_dir / "companies.yaml").write_text(
            (
                "companies:\n"
                "  tencent-holdings:\n"
                "    canonical_name: 腾讯控股有限公司\n"
                "    display_name: 腾讯控股\n"
                "    aliases: [腾讯控股]\n"
            ),
            encoding="utf-8",
        )
        (registry_dir / "industries.yaml").write_text(
            (
                "industries:\n"
                "  online-advertising:\n"
                "    canonical_name: 在线广告\n"
                "    display_name: 在线广告\n"
                "    aliases: [在线广告, 互联网广告]\n"
            ),
            encoding="utf-8",
        )

        company_plan_response = json.dumps({"companies": []})
        investment_page_plan_response = json.dumps({
            "industries": [
                {"name": "腾讯控股", "title": "腾讯控股", "action": "create"},
                {"name": "互联网广告", "title": "互联网广告", "action": "create"},
            ],
        })
        concept_plan_response = json.dumps({"create": [], "update": [], "related": []})
        industry_page_response = json.dumps({
            "brief": "平台广告变现行业",
            "content": "# 在线广告\n\n广告行业更新。",
        })

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    company_plan_response,
                    investment_page_plan_response,
                    concept_plan_response,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([industry_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki,
                tmp_path,
                "gpt-4o-mini",
                {"role": "system", "content": "You are a wiki agent."},
                {"role": "user", "content": "Document content."},
                "腾讯控股受益于互联网广告复苏。",
                "tencent-report",
                5,
            )

        assert (wiki / "industries" / "online-advertising.md").exists()
        assert not (wiki / "industries" / "腾讯控股.md").exists()
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[industries/online-advertising]] - 平台广告变现行业" in index_text

    @pytest.mark.asyncio
    async def test_generated_company_and_concept_pages_update_evidence_map(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "test-doc.md").write_text(
            "TSMC benefits from [[companies/TSMC]] and [[concepts/HBM]].",
            encoding="utf-8",
        )

        company_plan_response = json.dumps({
            "companies": [
                {"name": "TSMC", "title": "TSMC", "action": "create"},
            ],
        })
        concept_plan_response = json.dumps({
            "create": [{"name": "HBM", "title": "HBM"}],
            "update": [],
            "related": [],
        })
        company_page_response = json.dumps({
            "brief": "AI foundry bellwether",
            "content": (
                "# TSMC\n\n"
                "## Source Evidence\n"
                "- [[summaries/test-doc]] p.7: TSMC raises CoWoS capacity."
            ),
        })
        concept_page_response = json.dumps({
            "brief": "Memory bottleneck for AI accelerators",
            "content": (
                "# HBM\n\n"
                "## Source Evidence\n"
                "- [[summaries/test-doc]] p.12: HBM supply is a bottleneck."
            ),
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "TSMC benefits from [[companies/TSMC]] and [[concepts/HBM]]."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([company_plan_response, concept_plan_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response, concept_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        evidence = json.loads((wiki / "evidence_map.json").read_text(encoding="utf-8"))
        assert evidence["companies/TSMC.md"][0]["source"] == "summaries/test-doc.md"
        assert evidence["companies/TSMC.md"][0]["summary"] == "summaries/test-doc"
        assert evidence["companies/TSMC.md"][0]["page"] == "7"
        assert "TSMC raises CoWoS capacity" in evidence["companies/TSMC.md"][0]["snippet"]
        assert evidence["concepts/HBM.md"][0]["source"] == "summaries/test-doc.md"
        assert evidence["concepts/HBM.md"][0]["page"] == "12"
        assert "HBM supply is a bottleneck" in evidence["concepts/HBM.md"][0]["snippet"]

    @pytest.mark.asyncio
    async def test_company_update_preserves_prior_annual_report_page(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "tencent-2024.md").write_text(
            "# Tencent 2024\n\n[[companies/Tencent]]",
            encoding="utf-8",
        )
        (wiki / "companies" / "Tencent.md").write_text(
            "---\n"
            "sources: [summaries/tencent-2025.md]\n"
            "brief: 2025 growth and cash generation\n"
            "---\n\n"
            "# Tencent\n\n"
            "2025 revenue was RMB 751.8 billion.\n\n"
            "## Source Evidence\n"
            "- [[summaries/tencent-2025]] p.4: Revenue was RMB 751.8 billion.\n",
            encoding="utf-8",
        )
        (wiki / "evidence_map.json").write_text(
            json.dumps({
                "companies/Tencent.md": [
                    {
                        "source": "summaries/tencent-2025.md",
                        "summary": "summaries/tencent-2025",
                        "page": "4",
                        "snippet": "Revenue was RMB 751.8 billion.",
                    }
                ]
            }),
            encoding="utf-8",
        )
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n"
            "- [[summaries/tencent-2025]] (pageindex) - Tencent 2025 annual report\n\n"
            "## Companies\n"
            "- [[companies/Tencent]] - 2025 growth and cash generation\n\n"
            "## Concepts\n",
            encoding="utf-8",
        )

        company_plan_response = json.dumps({
            "companies": [
                {"name": "Tencent", "title": "Tencent", "action": "update"},
            ],
        })
        empty_investment_plan = json.dumps({})
        empty_concept_plan = json.dumps({"create": [], "update": [], "related": []})
        company_page_response = json.dumps({
            "brief": "2024 steady growth",
            "content": (
                "# Tencent\n\n"
                "2024 revenue was RMB 660.3 billion.\n\n"
                "## Source Evidence\n"
                "- [[summaries/tencent-2024]] p.4: Revenue was RMB 660.3 billion."
            ),
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Tencent 2024 annual report."}
        summary = "Tencent 2024 annual report cites [[companies/Tencent]]."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    company_plan_response,
                    empty_investment_plan,
                    empty_concept_plan,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "tencent-2024", 5, doc_type="pageindex",
            )

        company_text = (wiki / "companies" / "Tencent.md").read_text(encoding="utf-8")
        assert "2025 revenue was RMB 751.8 billion" in company_text
        assert "2024 revenue was RMB 660.3 billion" in company_text
        assert "summaries/tencent-2025.md" in company_text
        assert "summaries/tencent-2024.md" in company_text

        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "- [[companies/Tencent]] - 2025 growth and cash generation" in index_text
        assert "2024 steady growth" not in index_text

        evidence = json.loads((wiki / "evidence_map.json").read_text(encoding="utf-8"))
        company_sources = {entry["source"] for entry in evidence["companies/Tencent.md"]}
        assert company_sources == {"summaries/tencent-2025.md", "summaries/tencent-2024.md"}

    @pytest.mark.asyncio
    async def test_concept_update_preserves_prior_source_page(self, tmp_path):
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "value-investing": (
                "---\n"
                "sources: [summaries/book-a.md]\n"
                "brief: Invest with a margin of safety\n"
                "---\n\n"
                "# Value Investing\n\n"
                "Book A frames value investing around intrinsic value.\n\n"
                "## Source Evidence\n"
                "- [[summaries/book-a]] p.12: Intrinsic value anchors the process.\n"
            ),
        })
        (wiki / "summaries" / "book-b.md").write_text(
            "# Book B\n\n[[concepts/value-investing]]",
            encoding="utf-8",
        )
        (wiki / "evidence_map.json").write_text(
            json.dumps({
                "concepts/value-investing.md": [
                    {
                        "source": "summaries/book-a.md",
                        "summary": "summaries/book-a",
                        "page": "12",
                        "snippet": "Intrinsic value anchors the process.",
                    }
                ]
            }),
            encoding="utf-8",
        )
        (wiki / "index.md").write_text(
            "# Index\n\n"
            "## Documents\n"
            "- [[summaries/book-a]] (short) - Book A\n\n"
            "## Companies\n\n"
            "## Concepts\n"
            "- [[concepts/value-investing]] - Invest with a margin of safety\n",
            encoding="utf-8",
        )

        empty_company_plan = json.dumps({"companies": []})
        empty_investment_plan = json.dumps({})
        concept_plan_response = json.dumps({
            "create": [],
            "update": [{"name": "value-investing", "title": "Value Investing"}],
            "related": [],
        })
        concept_page_response = json.dumps({
            "brief": "Owner earnings and durable moats",
            "content": (
                "# Value Investing\n\n"
                "Book B emphasizes owner earnings and durable moats.\n\n"
                "## Source Evidence\n"
                "- [[summaries/book-b]] p.8: Owner earnings matter."
            ),
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Book B excerpt."}
        summary = "Book B adds to [[concepts/value-investing]]."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    empty_company_plan,
                    empty_investment_plan,
                    concept_plan_response,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "book-b", 5, doc_type="short",
            )

        concept_text = (wiki / "concepts" / "value-investing.md").read_text(encoding="utf-8")
        assert "Book A frames value investing around intrinsic value" in concept_text
        assert "Book B emphasizes owner earnings and durable moats" in concept_text
        assert "summaries/book-a.md" in concept_text
        assert "summaries/book-b.md" in concept_text

        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "- [[concepts/value-investing]] - Owner earnings and durable moats" in index_text

        evidence = json.loads((wiki / "evidence_map.json").read_text(encoding="utf-8"))
        concept_sources = {entry["source"] for entry in evidence["concepts/value-investing.md"]}
        assert concept_sources == {"summaries/book-a.md", "summaries/book-b.md"}

    @pytest.mark.asyncio
    async def test_suffix_variant_updates_existing_concept_and_backfills_source_evidence(self, tmp_path):
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "价值投资理论来源": "---\nsources: [summaries/old-doc.md]\n---\n\n# 价值投资理论来源\n\n既有概念内容。",
        })
        (wiki / "summaries" / "test-doc.md").write_text(
            "价值投资理论来源来自格雷厄姆与费雪框架。",
            encoding="utf-8",
        )

        company_plan_response = json.dumps({"companies": []})
        concept_plan_response = json.dumps({
            "create": [
                {
                    "name": "价值投资理论来源--85-格雷厄姆-15-费雪框架",
                    "title": "价值投资理论来源--85%格雷厄姆-15%费雪框架",
                }
            ],
            "update": [],
            "related": [],
        })
        concept_page_response = json.dumps({
            "brief": "价值投资方法论来源",
            "content": "# 价值投资理论来源\n\n来自格雷厄姆与费雪框架，并链接 [[summaries/test-doc]].",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "价值投资理论来源来自格雷厄姆与费雪框架。"

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([company_plan_response, concept_plan_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert not (wiki / "concepts" / "价值投资理论来源--85-格雷厄姆-15-费雪框架.md").exists()
        concept_text = (wiki / "concepts" / "价值投资理论来源.md").read_text(encoding="utf-8")
        assert "summaries/test-doc.md" in concept_text
        assert "## Source Evidence" in concept_text
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[concepts/价值投资理论来源--85-格雷厄姆-15-费雪框架]]" not in index_text

    @pytest.mark.asyncio
    async def test_invalid_company_plan_falls_back_to_summary_companies(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "test-doc.md").write_text(
            "首选股（Overweight）：台积电（Top Pick）、世芯（Alchip）。",
            encoding="utf-8",
        )

        concept_plan_response = json.dumps({
            "create": [],
            "update": [],
            "related": [],
        })
        company_page_response = json.dumps({
            "brief": "Company evidence from the report",
            "content": "# Company\n\nLinked to [[summaries/test-doc]].",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "首选股（Overweight）：台积电（Top Pick）、世芯（Alchip）。"

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion(["not json", concept_plan_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response, company_page_response]),
            ) as mock_acompletion,
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert mock_acompletion.await_count == 2
        assert (wiki / "companies" / "台积电.md").exists()
        assert (wiki / "companies" / "世芯.md").exists()
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[companies/台积电]] - Company evidence from the report" in index_text
        assert "[[companies/世芯]] - Company evidence from the report" in index_text

    @pytest.mark.asyncio
    async def test_invalid_concept_plan_falls_back_to_summary_headings(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)

        empty_company_plan = json.dumps({"companies": []})
        concept_page_response = json.dumps({
            "brief": "Durable investment concept",
            "content": "# Concept\n\nLinked to [[summaries/test-doc]].",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = (
            "## 先进封装：CoWoS与SoIC——算力核心\n"
            "## AI ASIC：定制化浪潮\n"
            "## 存储：HBM、DDR4与NOR短缺\n"
        )

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([empty_company_plan, "not json"]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response] * 6),
            ) as mock_acompletion,
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert mock_acompletion.await_count == 6
        assert (wiki / "concepts" / "Advanced_Packaging.md").exists()
        assert (wiki / "concepts" / "CoWoS.md").exists()
        assert (wiki / "concepts" / "SoIC.md").exists()
        assert (wiki / "concepts" / "AI_ASIC.md").exists()
        assert (wiki / "concepts" / "HBM.md").exists()
        assert (wiki / "concepts" / "NOR_Flash.md").exists()

    @pytest.mark.asyncio
    async def test_concept_plan_filters_company_names_and_adds_fallback_concepts(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)

        company_plan_response = json.dumps({
            "companies": [
                {"name": "TSMC", "title": "台积电", "action": "create"},
                {"name": "MPI", "title": "MPI", "action": "create"},
            ],
        })
        concept_plan_response = json.dumps({
            "create": [
                {"name": "台积电", "title": "台积电"},
                {"name": "MPI", "title": "MPI"},
                {"name": "ASIC", "title": "ASIC"},
            ],
            "update": [],
            "related": [],
        })
        page_response = json.dumps({
            "brief": "Generated page",
            "content": "# Page\n\nLinked to [[summaries/test-doc]].",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = (
            "首选股：台积电（Top Pick）、MPI。\n"
            "## AI ASIC：定制化浪潮\n"
            "## 存储：HBM、NOR短缺\n"
        )

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([company_plan_response, concept_plan_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([page_response] * 6),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert (wiki / "companies" / "台积电.md").exists()
        assert (wiki / "companies" / "MPI.md").exists()
        assert not (wiki / "concepts" / "台积电.md").exists()
        assert not (wiki / "concepts" / "MPI.md").exists()
        assert not (wiki / "concepts" / "ASIC.md").exists()
        assert (wiki / "concepts" / "AI_ASIC.md").exists()
        assert (wiki / "concepts" / "HBM.md").exists()

    @pytest.mark.asyncio
    async def test_empty_company_plan_falls_back_to_summary_companies(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)

        empty_company_plan = json.dumps({"companies": []})
        concept_plan_response = json.dumps({
            "create": [],
            "update": [],
            "related": [],
        })
        company_page_response = json.dumps({
            "brief": "Company evidence from the report",
            "content": "# Company\n\nLinked to [[summaries/test-doc]].",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "首选增持（OW）包括：台积电（Top Pick）、Alchip。"

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([empty_company_plan, concept_plan_response]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response, company_page_response]),
            ) as mock_acompletion,
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert mock_acompletion.await_count == 2
        assert (wiki / "companies" / "台积电.md").exists()
        assert (wiki / "companies" / "Alchip.md").exists()

    @pytest.mark.asyncio
    async def test_pageindex_company_placeholder_page_is_not_written(self, tmp_path):
        wiki = self._setup_wiki(tmp_path)
        (wiki / "summaries" / "test-doc.md").write_text(
            "---\ndoc_type: pageindex\nfull_text: sources/test-doc.json\n---\n\n"
            "# Test Doc\n\n[[companies/Tencent]]",
            encoding="utf-8",
        )

        company_plan_response = json.dumps({
            "companies": [
                {"name": "Tencent", "title": "Tencent", "action": "create"},
            ],
        })
        empty_investment_plan = json.dumps({})
        empty_concept_plan = json.dumps({"create": [], "update": [], "related": []})
        company_page_response = json.dumps({
            "brief": "Generated from a weak annual-report synthesis",
            "content": (
                "# Tencent\n\n"
                "Key financial numbers need to be extracted from the report.\n\n"
                "## Source Evidence\n"
                "- [[summaries/test-doc]]: TODO: add exact supporting claims and page references."
            ),
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "PageIndex annual report."}
        summary = "Tencent annual report overview without page evidence."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    company_plan_response,
                    empty_investment_plan,
                    empty_concept_plan,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([company_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="pageindex",
            )

        assert not (wiki / "companies" / "Tencent.md").exists()
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[companies/Tencent]]" not in index_text

    @pytest.mark.asyncio
    async def test_skipped_existing_concept_update_cleans_stale_placeholders(self, tmp_path):
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "margin": (
                "# Margin\n\nSee concepts/old-metric.\n\n"
                "## Source Evidence\n"
                "- [[summaries/old]]: TODO: add exact supporting claims and page references.\n"
            ),
        })

        empty_company_plan = json.dumps({"companies": []})
        empty_investment_plan = json.dumps({})
        concept_plan_response = json.dumps({
            "create": [],
            "update": [{"name": "margin", "title": "Margin"}],
            "related": [],
        })
        placeholder_update_response = json.dumps({
            "brief": "Placeholder update",
            "content": "# Margin\n\nTODO: add exact supporting claims and page references.",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([
                    empty_company_plan,
                    empty_investment_plan,
                    concept_plan_response,
                ]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([placeholder_update_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        text = (wiki / "concepts" / "margin.md").read_text(encoding="utf-8")
        assert "TODO" not in text
        assert "add exact supporting claims" not in text
        assert "concepts/old-metric" not in text
        assert "old-metric" in text

    @pytest.mark.asyncio
    async def test_related_adds_link_no_llm(self, tmp_path):
        """Plan has only related items. No acompletion calls should be made."""
        wiki = self._setup_wiki(tmp_path, existing_concepts={
            "transformer": "---\nsources: [old.pdf]\n---\n\n# Transformer\n\nContent about transformers.",
        })

        plan_response = json.dumps({
            "create": [],
            "update": [],
            "related": ["transformer"],
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        mock_acompletion = AsyncMock()
        with (
            patch("openkb.agent.compiler.completion", side_effect=_mock_completion([plan_response])),
            patch("openkb.agent.compiler.acompletion", mock_acompletion),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5,
            )
            # acompletion should never be called — related is code-only
            mock_acompletion.assert_not_called()

        # Verify link added to transformer page
        transformer_text = (wiki / "concepts" / "transformer.md").read_text()
        assert "[[summaries/test-doc]]" in transformer_text
        assert "summaries/test-doc.md" in transformer_text

    @pytest.mark.asyncio
    async def test_invalid_plan_falls_back_to_summary_concept_links(self, tmp_path):
        """If plan JSON is invalid, linked summary concepts are still materialized."""
        wiki = self._setup_wiki(tmp_path)

        concept_page_response = json.dumps({
            "brief": "Durable AI semiconductor concept",
            "content": "# Concept\n\n[[台积电]] ties to [[HBM]].",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "The report depends on [[concepts/HBM]] and [[concepts/CPO-共封装光学]]."

        with (
            patch("openkb.agent.compiler.completion", side_effect=_mock_completion(["not json"])),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response, concept_page_response]),
            ) as mock_acompletion,
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert mock_acompletion.await_count == 2
        assert (wiki / "concepts" / "HBM.md").exists()
        assert (wiki / "concepts" / "CPO-共封装光学.md").exists()
        hbm_text = (wiki / "concepts" / "HBM.md").read_text(encoding="utf-8")
        assert "[[台积电]]" not in hbm_text
        assert "台积电 ties to [[concepts/HBM]]" in hbm_text
        index_text = (wiki / "index.md").read_text(encoding="utf-8")
        assert "[[concepts/HBM]]" in index_text
        assert "[[concepts/CPO-共封装光学]]" in index_text

    @pytest.mark.asyncio
    async def test_invalid_plan_with_many_summary_links_unlinks_instead_of_exploding(self, tmp_path):
        """A bad plan plus many summary links should not create dozens of concepts."""
        wiki = self._setup_wiki(tmp_path)
        summary = " ".join(f"[[concepts/Company{i}]]" for i in range(12))
        (wiki / "summaries" / "test-doc.md").write_text(summary, encoding="utf-8")

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        mock_acompletion = AsyncMock()

        with (
            patch("openkb.agent.compiler.completion", side_effect=_mock_completion(["not json"])),
            patch("openkb.agent.compiler.acompletion", mock_acompletion),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        mock_acompletion.assert_not_called()
        assert list((wiki / "concepts").glob("*.md")) == []
        rewritten_summary = (wiki / "summaries" / "test-doc.md").read_text(encoding="utf-8")
        assert "[[concepts/" not in rewritten_summary
        assert "Company0" in rewritten_summary

    @pytest.mark.asyncio
    async def test_failed_concept_generation_unlinks_failed_summary_link(self, tmp_path):
        """A failed concept write should not leave a broken summary wikilink."""
        wiki = self._setup_wiki(tmp_path)
        summary = "This links [[concepts/HBM]]."
        (wiki / "summaries" / "test-doc.md").write_text(summary, encoding="utf-8")

        plan_response = json.dumps({
            "create": [{"name": "HBM", "title": "HBM"}],
            "update": [],
            "related": [],
        })
        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}

        async def failing_acompletion(*args, **kwargs):
            raise RuntimeError("network down")

        with (
            patch("openkb.agent.compiler.completion", side_effect=_mock_completion([plan_response])),
            patch("openkb.agent.compiler.acompletion", side_effect=failing_acompletion),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5, doc_type="local-long",
            )

        assert not (wiki / "concepts" / "HBM.md").exists()
        rewritten_summary = (wiki / "summaries" / "test-doc.md").read_text(encoding="utf-8")
        assert "[[concepts/HBM]]" not in rewritten_summary
        assert "HBM" in rewritten_summary

    @pytest.mark.asyncio
    async def test_fallback_list_format(self, tmp_path):
        """LLM returns a flat array instead of dict — treated as all create."""
        wiki = self._setup_wiki(tmp_path)

        plan_response = json.dumps([
            {"name": "attention", "title": "Attention"},
        ])
        concept_page_response = json.dumps({
            "brief": "A mechanism for focusing",
            "content": "# Attention\n\nA mechanism for focusing.",
        })

        system_msg = {"role": "system", "content": "You are a wiki agent."}
        doc_msg = {"role": "user", "content": "Document content."}
        summary = "Summary."

        with (
            patch("openkb.agent.compiler.completion", side_effect=_mock_completion([plan_response])),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_page_response]),
            ),
        ):
            await _compile_concepts(
                wiki, tmp_path, "gpt-4o-mini", system_msg, doc_msg,
                summary, "test-doc", 5,
            )

        # Verify concept was created (not updated)
        att_path = wiki / "concepts" / "attention.md"
        assert att_path.exists()
        att_text = att_path.read_text()
        assert "sources: [summaries/test-doc.md]" in att_text
        assert "Attention" in att_text


class TestBriefIntegration:
    @pytest.mark.asyncio
    async def test_short_doc_briefs_in_index_and_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nContent.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        summary_resp = json.dumps({
            "brief": "A paper about transformers",
            "content": "# Summary\n\nThis paper discusses transformers.",
        })
        plan_resp = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        concept_resp = json.dumps({
            "brief": "NN architecture using self-attention",
            "content": "# Transformer\n\nA neural network architecture.",
        })

        with (
            patch(
                "openkb.agent.compiler.completion",
                side_effect=_mock_completion([summary_resp, plan_resp]),
            ),
            patch(
                "openkb.agent.compiler.acompletion",
                side_effect=_mock_acompletion([concept_resp]),
            ),
        ):
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Summary frontmatter has doc_type and full_text
        summary_text = (wiki / "summaries" / "test-doc.md").read_text()
        assert "doc_type: short" in summary_text
        assert "full_text: sources/test-doc.md" in summary_text

        # Concept frontmatter has brief
        concept_text = (wiki / "concepts" / "transformer.md").read_text()
        assert "brief: NN architecture using self-attention" in concept_text

        # Index has briefs
        index_text = (wiki / "index.md").read_text()
        assert "- A paper about transformers" in index_text
        assert "- NN architecture using self-attention" in index_text
