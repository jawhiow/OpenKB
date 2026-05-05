from __future__ import annotations

from openkb.ocr.normalize import normalize_ocr_payloads


def test_normalize_ocr_payloads_returns_pages_json_and_pageindex_markdown():
    payloads = [
        {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "# Page One\n\n![chart](figures/chart.png)\nRevenue up.",
                            "images": {"figures/chart.png": "https://example.com/chart.png"},
                        }
                    }
                ]
            }
        },
        {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": "## Page Two\n\nMargin expanded.",
                            "images": {},
                        }
                    }
                ]
            }
        },
    ]

    pages, pageindex_input = normalize_ocr_payloads(payloads, doc_name="report")

    assert pages == [
        {
            "page": 1,
            "content": "# Page One\n\n![chart](sources/images/report/ocr/page-1/figures/chart.png)\nRevenue up.",
            "images": [
                {
                    "path": "sources/images/report/ocr/page-1/figures/chart.png",
                    "url": "https://example.com/chart.png",
                }
            ],
        },
        {
            "page": 2,
            "content": "## Page Two\n\nMargin expanded.",
            "images": [],
        },
    ]
    assert "## Page 1" in pageindex_input
    assert "## Page 2" in pageindex_input
    assert "sources/images/report/ocr/page-1/figures/chart.png" in pageindex_input
    assert "Margin expanded." in pageindex_input


def test_normalize_ocr_payloads_skips_empty_entries_and_keeps_page_order():
    payloads = [
        {"result": {"layoutParsingResults": []}},
        {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"text": "First kept page", "images": {}}},
                    {"markdown": {"text": "Second kept page", "images": {}}},
                ]
            }
        },
    ]

    pages, pageindex_input = normalize_ocr_payloads(payloads, doc_name="doc")

    assert [page["page"] for page in pages] == [1, 2]
    assert pages[0]["content"] == "First kept page"
    assert pages[1]["content"] == "Second kept page"
    assert pageindex_input.index("First kept page") < pageindex_input.index("Second kept page")
