from __future__ import annotations

import json

from openkb.ocr.pipeline import prepare_ocr_artifacts
from openkb.state import ocr_cache_manifest_path, ocr_cache_pageindex_input_path, ocr_cache_pages_path


class FakeJob:
    def __init__(self):
        self.logs: list[tuple[str, str]] = []

    def add_log(self, message, level="info"):
        self.logs.append((level, message))


def _make_kb(tmp_path):
    kb_dir = tmp_path / "kb"
    (kb_dir / ".openkb").mkdir(parents=True)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "ocr_chunk_pages: 100\nocr_default_model: PaddleOCR-VL-1.5\n",
        encoding="utf-8",
    )
    (kb_dir / ".env").write_text("PADDLEOCR_TOKEN=test-token\n", encoding="utf-8")
    return kb_dir


def test_prepare_ocr_artifacts_reuses_ready_cache(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    file_hash = "abc123"

    pages_path = ocr_cache_pages_path(kb_dir, file_hash)
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps([{"page": 1, "content": "cached", "images": []}]), encoding="utf-8")
    md_path = ocr_cache_pageindex_input_path(kb_dir, file_hash)
    md_path.write_text("## Page 1\n\ncached", encoding="utf-8")
    manifest_path = ocr_cache_manifest_path(kb_dir, file_hash)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"status": "ready", "file_hash": file_hash, "ocr_model": "PaddleOCR-VL-1.5"}),
        encoding="utf-8",
    )

    def fail(*args, **kwargs):
        raise AssertionError("OCR should not run when cache is ready")

    monkeypatch.setattr("openkb.ocr.pipeline.run_ocr_chunks", fail)

    artifacts = prepare_ocr_artifacts(pdf_path, kb_dir, "doc", file_hash)

    assert artifacts["pages_path"] == pages_path
    assert artifacts["pageindex_input_path"] == md_path


def test_prepare_ocr_artifacts_logs_cache_reuse_to_job_details(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    file_hash = "abc123"
    job = FakeJob()

    pages_path = ocr_cache_pages_path(kb_dir, file_hash)
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps([{"page": 1, "content": "cached", "images": []}]), encoding="utf-8")
    md_path = ocr_cache_pageindex_input_path(kb_dir, file_hash)
    md_path.write_text("## Page 1\n\ncached", encoding="utf-8")
    manifest_path = ocr_cache_manifest_path(kb_dir, file_hash)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"status": "ready", "file_hash": file_hash, "ocr_model": "PaddleOCR-VL-1.5"}),
        encoding="utf-8",
    )

    def fail(*args, **kwargs):
        raise AssertionError("OCR should not run when cache is ready")

    monkeypatch.setattr("openkb.ocr.pipeline.run_ocr_chunks", fail)

    prepare_ocr_artifacts(pdf_path, kb_dir, "doc", file_hash, job=job)

    messages = [message for _level, message in job.logs]
    assert any("OCR cache hit" in message for message in messages)


def test_prepare_ocr_artifacts_force_reruns_even_when_cache_is_ready(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    file_hash = "abc123"

    pages_path = ocr_cache_pages_path(kb_dir, file_hash)
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps([{"page": 1, "content": "cached", "images": []}]), encoding="utf-8")
    md_path = ocr_cache_pageindex_input_path(kb_dir, file_hash)
    md_path.write_text("## Page 1\n\ncached", encoding="utf-8")
    manifest_path = ocr_cache_manifest_path(kb_dir, file_hash)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"status": "ready", "file_hash": file_hash, "ocr_model": "PaddleOCR-VL-1.5"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "openkb.ocr.pipeline._write_pdf_chunk_files",
        lambda pdf_path, chunk_plan, chunk_dir: [chunk_dir / "chunk-1.pdf"],
    )
    monkeypatch.setattr(
        "openkb.ocr.pipeline.run_ocr_chunks",
        lambda *args, **kwargs: [
            {"payloads": [{"result": {"layoutParsingResults": [{"markdown": {"text": "fresh OCR", "images": {}}}]}}]}
        ],
    )
    monkeypatch.setattr("openkb.ocr.pipeline.get_pdf_page_count", lambda _path: 1)

    artifacts = prepare_ocr_artifacts(pdf_path, kb_dir, "doc", file_hash, force=True)

    assert artifacts["pages_path"] == pages_path
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    assert pages[0]["content"] == "fresh OCR"


def test_prepare_ocr_artifacts_writes_normalized_outputs(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    file_hash = "deadbeef"

    monkeypatch.setattr(
        "openkb.ocr.pipeline._write_pdf_chunk_files",
        lambda pdf_path, chunk_plan, chunk_dir: [chunk_dir / "chunk-1.pdf"],
    )
    monkeypatch.setattr(
        "openkb.ocr.pipeline.run_ocr_chunks",
        lambda *args, **kwargs: [
            {"payloads": [{"result": {"layoutParsingResults": [{"markdown": {"text": "OCR page", "images": {}}}]}}]}
        ],
    )
    monkeypatch.setattr(
        "openkb.ocr.pipeline.get_pdf_page_count",
        lambda _path: 1,
    )

    artifacts = prepare_ocr_artifacts(pdf_path, kb_dir, "doc", file_hash)

    assert artifacts["pages_path"].exists()
    assert artifacts["pageindex_input_path"].exists()
    pages = json.loads(artifacts["pages_path"].read_text(encoding="utf-8"))
    assert pages[0]["content"] == "OCR page"
    assert "OCR page" in artifacts["pageindex_input_path"].read_text(encoding="utf-8")


def test_prepare_ocr_artifacts_logs_plan_and_normalized_outputs_to_job_details(tmp_path, monkeypatch):
    kb_dir = _make_kb(tmp_path)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    file_hash = "deadbeef"
    job = FakeJob()
    captured: dict[str, object] = {}

    def fake_write_chunks(pdf_path, chunk_plan, chunk_dir):
        captured["chunk_plan"] = chunk_plan
        return [chunk_dir / "chunk-1.pdf", chunk_dir / "chunk-2.pdf"]

    def fake_run_chunks(chunk_files, **kwargs):
        captured["chunk_files"] = chunk_files
        captured["job"] = kwargs.get("job")
        return [
            {"payloads": [{"result": {"layoutParsingResults": [{"markdown": {"text": "OCR page one", "images": {}}}]}}]},
            {"payloads": [{"result": {"layoutParsingResults": [{"markdown": {"text": "OCR page two", "images": {}}}]}}]},
        ]

    monkeypatch.setattr("openkb.ocr.pipeline._write_pdf_chunk_files", fake_write_chunks)
    monkeypatch.setattr("openkb.ocr.pipeline.run_ocr_chunks", fake_run_chunks)
    monkeypatch.setattr("openkb.ocr.pipeline.get_pdf_page_count", lambda _path: 101)

    prepare_ocr_artifacts(pdf_path, kb_dir, "doc", file_hash, job=job)

    assert captured["job"] is job
    messages = [message for _level, message in job.logs]
    assert any("Preparing OCR artifacts" in message and "PaddleOCR-VL-1.5" in message for message in messages)
    assert any("OCR page plan: 101 page(s) in 2 chunk(s)" in message for message in messages)
    assert any("OCR chunk files prepared: 2" in message for message in messages)
    assert any("Running PaddleOCR: 2 chunk(s)" in message for message in messages)
    assert any("Normalized OCR output: 2 page(s)" in message for message in messages)
    assert any("OCR artifacts cached" in message for message in messages)
