"""Tests for openkb.indexer."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openkb.indexer import IndexResult, index_long_document, index_ocr_with_local_pageindex


class TestIndexLongDocument:
    def _make_fake_collection(self, doc_id: str, sample_tree: dict):
        """Build a mock Collection that returns the sample_tree fixture data."""
        col = MagicMock()
        col.add.return_value = doc_id

        # get_document(doc_id, include_text=True) returns full document
        col.get_document.return_value = {
            "doc_id": doc_id,
            "doc_name": sample_tree["doc_name"],
            "doc_description": sample_tree["doc_description"],
            "doc_type": "pdf",
            "structure": sample_tree["structure"],
        }

        # get_page_content returns empty list by default (overridden per test as needed)
        col.get_page_content.return_value = []
        return col

    def _fake_pages(self):
        return [
            {"page": 1, "content": "Page one text.", "images": []},
            {"page": 2, "content": "Page two text.", "images": []},
        ]

    def test_returns_index_result(self, kb_dir, sample_tree, tmp_path):
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()):
            result = index_long_document(pdf_path, kb_dir)

        assert isinstance(result, IndexResult)
        assert result.doc_id == doc_id
        assert result.description == sample_tree["doc_description"]
        assert result.tree is not None

    def test_source_page_written_as_json(self, kb_dir, sample_tree, tmp_path):
        """Long doc source should be written as JSON, not markdown."""
        import json as json_mod
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col
        # Mock get_page_content to return page data
        fake_col.get_page_content.return_value = [
            {"page": 1, "content": "Page one text."},
            {"page": 2, "content": "Page two text."},
        ]

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()):
            index_long_document(pdf_path, kb_dir)

        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert json_file.exists()
        assert not (kb_dir / "wiki" / "sources" / "sample.md").exists()
        data = json_mod.loads(json_file.read_text())
        assert len(data) == 2
        assert data[0]["page"] == 1
        assert data[0]["content"] == "Page one text."

    def test_summary_page_written(self, kb_dir, sample_tree, tmp_path):
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()):
            index_long_document(pdf_path, kb_dir)

        summary_file = kb_dir / "wiki" / "summaries" / "sample.md"
        assert summary_file.exists()
        content = summary_file.read_text(encoding="utf-8")
        assert "doc_type: pageindex" in content
        assert "Summary:" in content

    def test_localclient_called_with_index_config(self, kb_dir, sample_tree, tmp_path):
        """LocalClient must be created with the correct IndexConfig flags."""
        doc_id = "xyz-456"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client) as mock_cls, \
             patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()):
            index_long_document(pdf_path, kb_dir)

        # Verify PageIndexClient was instantiated with correct IndexConfig
        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        ic = kwargs.get("index_config")
        assert ic is not None, "index_config must be passed to PageIndexClient"
        assert ic.if_add_node_text is True
        assert ic.if_add_node_summary is True
        assert ic.if_add_doc_description is True

    def test_compatible_client_without_index_config(self, kb_dir, sample_tree, tmp_path):
        """Falls back to the published PageIndex client API when legacy kwargs fail."""
        doc_id = "compat-123"
        compat_client = MagicMock()
        compat_client.index.return_value = doc_id
        compat_client.get_document.return_value = json.dumps(
            {
                "doc_id": doc_id,
                "doc_name": sample_tree["doc_name"],
                "doc_description": sample_tree["doc_description"],
                "type": "pdf",
                "page_count": 2,
            }
        )
        compat_client.get_document_structure.return_value = json.dumps(sample_tree["structure"])

        pdf_path = tmp_path / "compat.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        def fake_client_factory(*args, **kwargs):
            if "storage_path" in kwargs or "index_config" in kwargs:
                raise TypeError("legacy kwargs not supported")
            return compat_client

        with patch("openkb.indexer.PageIndexClient", side_effect=fake_client_factory), \
             patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()):
            result = index_long_document(pdf_path, kb_dir)

        assert isinstance(result, IndexResult)
        assert result.doc_id == doc_id
        compat_client.index.assert_called_once_with(str(pdf_path), mode="pdf")

    def test_compatible_client_with_cloud_sdk(self, kb_dir, sample_tree, tmp_path, monkeypatch):
        """Supports the cloud SDK shape exposed by the current PageIndex Python package."""
        doc_id = "pi-cloud-123"
        cloud_client = MagicMock()
        cloud_client.submit_document.return_value = {"doc_id": doc_id}
        cloud_client.get_tree.return_value = {
            "doc_id": doc_id,
            "status": "completed",
            "result": sample_tree["structure"],
        }
        cloud_client.get_document.return_value = {
            "id": doc_id,
            "name": "sample.pdf",
            "description": sample_tree["doc_description"],
            "status": "completed",
            "pageNum": 2,
        }
        cloud_client.get_ocr.return_value = {
            "doc_id": doc_id,
            "status": "completed",
            "result": [
                {"page": 1, "content": "Page one text.", "images": []},
                {"page": 2, "content": "Page two text.", "images": []},
            ],
        }

        pdf_path = tmp_path / "cloud.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        def fake_client_factory(*args, **kwargs):
            if "storage_path" in kwargs or "index_config" in kwargs or "workspace" in kwargs or "model" in kwargs:
                raise TypeError("unsupported kwargs for cloud sdk")
            return cloud_client

        monkeypatch.setenv("PAGEINDEX_API_KEY", "pi-test-key")

        with patch("openkb.indexer.PageIndexClient", side_effect=fake_client_factory), \
             patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()):
            result = index_long_document(pdf_path, kb_dir)

        assert isinstance(result, IndexResult)
        assert result.doc_id == doc_id
        assert result.description == sample_tree["doc_description"]
        cloud_client.submit_document.assert_called_once_with(str(pdf_path))
        cloud_client.get_tree.assert_called_once_with(doc_id, node_summary=True)
        cloud_client.get_ocr.assert_called_once_with(doc_id, format="page")

    def test_indexes_ocr_markdown_with_local_pageindex_and_preserves_pages_json(self, kb_dir, sample_tree, tmp_path):
        pages_path = tmp_path / "pages.json"
        pageindex_input_path = tmp_path / "pageindex_input.md"
        output_tree_path = kb_dir / ".openkb" / "pageindex-local" / "sample-tree.json"
        pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR page one", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        pageindex_input_path.write_text("## Page 1\n\nOCR page one", encoding="utf-8")

        def fake_run(input_path, output_path, **kwargs):
            assert input_path == pageindex_input_path
            assert output_path == output_tree_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "doc": {
                            "id": "local-doc-1",
                            "name": "sample",
                            "description": "Local PageIndex summary",
                        },
                        "tree": sample_tree["structure"],
                    }
                ),
                encoding="utf-8",
            )

        with patch("openkb.indexer.run_pageindex_local", side_effect=fake_run):
            result = index_ocr_with_local_pageindex(
                "sample",
                pages_path,
                pageindex_input_path,
                kb_dir,
                model="gpt-5.4",
                output_tree_path=output_tree_path,
            )

        assert result.doc_id == "local-doc-1"
        assert result.description == "Local PageIndex summary"
        assert json.loads((kb_dir / "wiki" / "sources" / "sample.json").read_text(encoding="utf-8"))[0]["content"] == "OCR page one"
        summary_text = (kb_dir / "wiki" / "summaries" / "sample.md").read_text(encoding="utf-8")
        assert "doc_type: pageindex" in summary_text
        assert "Local PageIndex summary" not in summary_text

    def test_index_ocr_with_local_pageindex_logs_runtime_outputs_to_job_details(self, kb_dir, sample_tree, tmp_path):
        pages_path = tmp_path / "pages.json"
        pageindex_input_path = tmp_path / "pageindex_input.md"
        output_tree_path = kb_dir / ".openkb" / "pageindex-local" / "sample-tree.json"
        pages_path.write_text(
            json.dumps([{"page": 1, "content": "OCR page one", "images": []}], ensure_ascii=False),
            encoding="utf-8",
        )
        pageindex_input_path.write_text("## Page 1\n\nOCR page one", encoding="utf-8")

        class FakeJob:
            def __init__(self):
                self.logs: list[tuple[str, str]] = []

            def add_log(self, message, level="info"):
                self.logs.append((level, message))

        job = FakeJob()

        def fake_run(input_path, output_path, **kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "doc": {
                            "id": "local-doc-1",
                            "name": "sample",
                            "description": "Local PageIndex summary",
                        },
                        "tree": sample_tree["structure"],
                    }
                ),
                encoding="utf-8",
            )

        with patch("openkb.indexer.run_pageindex_local", side_effect=fake_run):
            index_ocr_with_local_pageindex(
                "sample",
                pages_path,
                pageindex_input_path,
                kb_dir,
                model="gpt-5.4",
                output_tree_path=output_tree_path,
                job=job,
            )

        messages = [message for _level, message in job.logs]
        assert any("Running local PageIndex" in message for message in messages)
        assert any("Local PageIndex tree written" in message for message in messages)
        assert any("Local PageIndex normalized: doc_id=local-doc-1" in message for message in messages)
        assert any("Local PageIndex artifacts written" in message for message in messages)
