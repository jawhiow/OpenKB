"""Tests for openkb.tree_renderer."""
from __future__ import annotations

import pytest

from openkb.pageindex_local.translate import normalize_pageindex_local_tree
from openkb.tree_renderer import render_summary_md


# ---------------------------------------------------------------------------
# render_summary_md
# ---------------------------------------------------------------------------


class TestRenderSummaryMd:
    def test_has_yaml_frontmatter(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert output.startswith("---\n")
        assert "doc_type: pageindex" in output
        assert "full_text: sources/Sample Document.json" in output

    def test_top_level_nodes_are_h1(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "# Introduction" in output
        assert "# Conclusion" in output

    def test_nested_nodes_are_h2(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "## Background" in output
        assert "## Motivation" in output

    def test_page_range_included(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "(pages 0–120)" in output
        assert "(pages 121–200)" in output

    def test_summary_included_not_text(self, sample_tree):
        output = render_summary_md(sample_tree, "Sample Document", "doc-abc")
        assert "Summary: Overview of the document topic." in output
        assert "Summary: Historical context." in output
        # Raw text should NOT appear in summary view
        assert "This document introduces the core concepts of the system." not in output


def test_normalize_pageindex_local_tree_accepts_nested_children_and_page_ranges():
    payload = {
        "doc": {"id": "local-doc-1", "name": "OCR Report", "description": "OCR-generated tree"},
        "tree": [
            {
                "heading": "Executive Summary",
                "page_start": 1,
                "page_end": 2,
                "node_summary": "Main findings.",
                "children": [
                    {
                        "heading": "Revenue",
                        "page_start": 2,
                        "page_end": 4,
                        "node_summary": "Revenue details.",
                    }
                ],
            }
        ],
    }

    tree = normalize_pageindex_local_tree(payload, fallback_doc_name="report")

    assert tree == {
        "doc_name": "OCR Report",
        "doc_description": "OCR-generated tree",
        "structure": [
            {
                "title": "Executive Summary",
                "node_id": "local-1",
                "start_index": 1,
                "end_index": 2,
                "summary": "Main findings.",
                "nodes": [
                    {
                        "title": "Revenue",
                        "node_id": "local-1-1",
                        "start_index": 2,
                        "end_index": 4,
                        "summary": "Revenue details.",
                        "nodes": [],
                    }
                ],
            }
        ],
    }


def test_render_summary_md_uses_existing_page_range_format_for_pageindex_local_tree():
    tree = {
        "structure": [
            {"title": "Section", "start_index": 1, "end_index": 3, "summary": "Summary.", "nodes": []},
        ]
    }

    output = render_summary_md(tree, "report", "local-doc-1")

    assert "# Section (pages 1\u20133)" in output
