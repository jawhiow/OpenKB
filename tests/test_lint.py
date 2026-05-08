"""Tests for openkb.lint (Task 13)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openkb.lint import (
    check_index_sync,
    find_broken_links,
    find_incomplete_entries,
    find_investment_quality_issues,
    find_missing_entries,
    find_orphans,
    run_structural_lint,
)


def _make_wiki(tmp_path: Path) -> Path:
    """Create a minimal wiki directory structure."""
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "summaries").mkdir(parents=True)
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "companies").mkdir(parents=True)
    (wiki / "reports").mkdir(parents=True)
    (wiki / "index.md").write_text(
        "# Index\n\n## Documents\n\n## Concepts\n", encoding="utf-8"
    )
    return wiki


class TestFindBrokenLinks:
    def test_no_broken_links(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "attention.md").write_text("# Attention")
        (wiki / "summaries" / "paper.md").write_text(
            "Refers to [[concepts/attention]]", encoding="utf-8"
        )

        result = find_broken_links(wiki)

        assert result == []

    def test_detects_broken_link(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "paper.md").write_text(
            "See [[concepts/missing_concept]]", encoding="utf-8"
        )

        result = find_broken_links(wiki)

        assert len(result) == 1
        assert "missing_concept" in result[0]

    def test_multiple_broken_links(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "doc.md").write_text(
            "See [[concepts/foo]] and [[concepts/bar]]", encoding="utf-8"
        )

        result = find_broken_links(wiki)

        assert len(result) == 2

    def test_no_links_means_no_errors(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "paper.md").write_text("No wikilinks here.")

        result = find_broken_links(wiki)

        assert result == []


class TestFindOrphans:
    def test_linked_page_is_not_orphan(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "attention.md").write_text("# Attention")
        (wiki / "summaries" / "paper.md").write_text(
            "See [[concepts/attention]]", encoding="utf-8"
        )

        result = find_orphans(wiki)

        assert "concepts/attention" not in result

    def test_isolated_page_is_orphan(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "lonely.md").write_text("# Lonely page with no links.")

        result = find_orphans(wiki)

        assert any("lonely" in r for r in result)

    def test_page_with_outgoing_links_not_orphan(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "linking.md").write_text("See [[other/page]].")
        # linking.md has outgoing links so it's not orphaned even if unreferenced

        result = find_orphans(wiki)

        assert "concepts/linking" not in result

    def test_empty_wiki_has_no_orphans(self, tmp_path):
        wiki = _make_wiki(tmp_path)

        result = find_orphans(wiki)

        assert result == []

    def test_reports_are_not_orphans(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "reports" / "lint_20260503_092920.md").write_text("# Lint Report")

        result = find_orphans(wiki)

        assert "reports/lint_20260503_092920" not in result


class TestFindMissingEntries:
    def test_no_missing_entries(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "paper.pdf").write_bytes(b"PDF content")
        (wiki / "sources" / "paper.md").write_text("# Paper")

        result = find_missing_entries(raw, wiki)

        assert result == []

    def test_detects_missing_entry(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "unprocessed.pdf").write_bytes(b"PDF content")
        # No corresponding wiki entry

        result = find_missing_entries(raw, wiki)

        assert "unprocessed.pdf" in result

    def test_summary_counts_as_entry(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "longdoc.pdf").write_bytes(b"PDF")
        (wiki / "summaries" / "longdoc.md").write_text("# Long doc summary")

        result = find_missing_entries(raw, wiki)

        assert "longdoc.pdf" not in result

    def test_empty_raw_means_no_missing(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        result = find_missing_entries(raw, wiki)

        assert result == []

    def test_source_without_summary_is_incomplete(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "doc.docx").write_bytes(b"DOCX")
        (wiki / "sources" / "doc.md").write_text("# Converted", encoding="utf-8")

        result = find_incomplete_entries(raw, wiki)

        assert result == ["doc.docx"]

    def test_structural_lint_reports_source_without_summary(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "doc.docx").write_bytes(b"DOCX")
        (wiki / "sources" / "doc.md").write_text("# Converted", encoding="utf-8")

        report = run_structural_lint(tmp_path)

        assert "### Incomplete Wiki Entries (1)" in report
        assert "- doc.docx" in report


class TestCheckIndexSync:
    def test_clean_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "paper.md").write_text("# Paper")
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/paper]]\n\n## Concepts\n"
        )

        result = check_index_sync(wiki)

        assert result == []

    def test_broken_index_link(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/ghost]]\n"
        )

        result = check_index_sync(wiki)

        assert any("ghost" in issue for issue in result)

    def test_page_not_in_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "unlisted.md").write_text("# Unlisted")
        # index.md has no mention of unlisted

        result = check_index_sync(wiki)

        assert any("unlisted" in issue for issue in result)

    def test_broken_links_ignore_legacy_investment_schema_pages(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "themes").mkdir()
        (wiki / "themes" / "ai-capex.md").write_text("See [[concepts/missing]]")

        result = find_broken_links(wiki)

        assert result == []

    def test_orphans_ignore_legacy_investment_schema_pages(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "themes").mkdir()
        (wiki / "themes" / "ai-capex.md").write_text("# AI CAPEX")

        result = find_orphans(wiki)

        assert "themes/ai-capex" not in result

    def test_company_page_not_in_index(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "companies" / "tsmc.md").write_text("# TSMC")

        result = check_index_sync(wiki)

        assert any("companies/tsmc.md not mentioned in index.md" in issue for issue in result)

    def test_legacy_investment_schema_page_is_not_active_index_sync_surface(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "themes").mkdir()
        (wiki / "themes" / "ai-capex.md").write_text("# AI CAPEX")

        result = check_index_sync(wiki)

        assert not any("themes/ai-capex.md not mentioned in index.md" in issue for issue in result)

    def test_missing_index_md(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        result = check_index_sync(wiki)

        assert any("does not exist" in issue for issue in result)


class TestInvestmentQualityIssues:
    def test_detects_company_like_concept_page(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "台积电.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n"
            "# 台积电\n\n台积电是全球最大晶圆代工厂，大摩给予超配评级，目标价 NT$2588。",
            encoding="utf-8",
        )

        issues = find_investment_quality_issues(wiki)

        assert any("company-like concept page" in issue for issue in issues)
        assert any("concepts/台积电.md" in issue for issue in issues)

    def test_does_not_flag_theme_page_with_company_exposure_section(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "concepts" / "CoWoS.md").write_text(
            "---\nsources: [summaries/report.md]\n---\n\n"
            "# CoWoS\n\n"
            "CoWoS is an advanced packaging technology for AI accelerators.\n\n"
            "## Why It Matters\n"
            "It connects compute chiplets and HBM through an interposer.\n\n"
            "## Company Exposure\n"
            "TSMC (2330.TW, OW) 是主要代工厂，ASE (3711.TW, OW) 是后段封装供应商。",
            encoding="utf-8",
        )

        issues = find_investment_quality_issues(wiki)

        assert not any("company-like concept page" in issue for issue in issues)

    def test_detects_concept_explosion_from_one_summary(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        (wiki / "summaries" / "report.md").write_text("# Report", encoding="utf-8")
        for i in range(16):
            (wiki / "concepts" / f"company{i}.md").write_text(
                f"---\nsources: [summaries/report.md]\n---\n\n# Company {i}",
                encoding="utf-8",
            )

        issues = find_investment_quality_issues(wiki, max_concepts_per_summary=12)

        assert any("concept explosion" in issue for issue in issues)
        assert any("summaries/report.md" in issue for issue in issues)


class TestRunStructuralLint:
    def test_returns_markdown_report(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        report = run_structural_lint(tmp_path)

        assert "Structural Lint Report" in report
        assert "Broken Links" in report
        assert "Orphaned Pages" in report
        assert "Raw Files Without Wiki Entry" in report
        assert "Index Sync" in report

    def test_clean_kb_shows_no_issues(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()

        report = run_structural_lint(tmp_path)

        assert "No broken links found" in report
        assert "No orphaned pages found" in report
        assert "All raw files have wiki entries" in report

    def test_report_includes_broken_link_details(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (wiki / "summaries" / "doc.md").write_text("See [[concepts/missing]]")

        report = run_structural_lint(tmp_path)

        assert "missing" in report

    def test_report_includes_investment_quality_section(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        raw = tmp_path / "raw"
        raw.mkdir()
        (wiki / "concepts" / "台积电.md").write_text(
            "# 台积电\n\n台积电是全球最大晶圆代工厂，大摩给予超配评级。",
            encoding="utf-8",
        )

        report = run_structural_lint(tmp_path)

        assert "Investment KB Quality" in report
        assert "company-like concept page" in report
