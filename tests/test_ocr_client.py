from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openkb.ocr.client import (
    download_ocr_jsonl,
    run_ocr_chunks,
    submit_ocr_job,
    wait_for_ocr_job,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text or f"HTTP {self.status_code}")


def test_submit_ocr_job_posts_local_file_and_returns_job_id(tmp_path):
    pdf_path = tmp_path / "chunk-1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, data=None, files=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["filename"] = Path(files["file"].name).name
        return _FakeResponse(200, {"data": {"jobId": "job-123"}})

    requests_module = SimpleNamespace(post=fake_post)

    job_id = submit_ocr_job(
        pdf_path,
        token="token-abc",
        model="PaddleOCR-VL-1.5",
        optional_payload={"useChartRecognition": False},
        requests_module=requests_module,
    )

    assert job_id == "job-123"
    assert captured["headers"] == {"Authorization": "bearer token-abc"}
    assert captured["data"]["model"] == "PaddleOCR-VL-1.5"
    assert json.loads(captured["data"]["optionalPayload"]) == {"useChartRecognition": False}
    assert captured["filename"] == "chunk-1.pdf"


def test_wait_for_ocr_job_reports_progress_and_returns_done_payload():
    responses = iter(
        [
            _FakeResponse(200, {"data": {"state": "pending"}}),
            _FakeResponse(
                200,
                {
                    "data": {
                        "state": "running",
                        "extractProgress": {"totalPages": 100, "extractedPages": 25},
                    }
                },
            ),
            _FakeResponse(
                200,
                {
                    "data": {
                        "state": "done",
                        "extractProgress": {"totalPages": 100, "extractedPages": 100},
                        "resultUrl": {"jsonUrl": "https://example.com/result.jsonl"},
                    }
                },
            ),
        ]
    )
    progress_updates: list[tuple[int, int]] = []

    def fake_get(url, headers=None):
        return next(responses)

    requests_module = SimpleNamespace(get=fake_get)

    payload = wait_for_ocr_job(
        "job-123",
        token="token-abc",
        requests_module=requests_module,
        sleep_fn=lambda _seconds: None,
        progress_callback=lambda current, total: progress_updates.append((current, total)),
    )

    assert payload["resultUrl"]["jsonUrl"] == "https://example.com/result.jsonl"
    assert progress_updates == [(25, 100), (100, 100)]


def test_download_ocr_jsonl_parses_non_empty_lines():
    line_one = json.dumps({"result": {"layoutParsingResults": [{"markdown": {"text": "Page 1", "images": {}}}]}})
    line_two = json.dumps({"result": {"layoutParsingResults": [{"markdown": {"text": "Page 2", "images": {}}}]}})

    def fake_get(url):
        return _FakeResponse(200, text=f"{line_one}\n\n{line_two}\n")

    requests_module = SimpleNamespace(get=fake_get)

    payloads = download_ocr_jsonl("https://example.com/result.jsonl", requests_module=requests_module)

    assert len(payloads) == 2
    assert payloads[0]["result"]["layoutParsingResults"][0]["markdown"]["text"] == "Page 1"
    assert payloads[1]["result"]["layoutParsingResults"][0]["markdown"]["text"] == "Page 2"


def test_run_ocr_chunks_retries_failed_chunk_once_and_updates_progress(tmp_path, monkeypatch):
    chunk_a = tmp_path / "chunk-a.pdf"
    chunk_b = tmp_path / "chunk-b.pdf"
    chunk_a.write_bytes(b"%PDF-1.4 a")
    chunk_b.write_bytes(b"%PDF-1.4 b")

    submit_attempts: list[str] = []

    def fake_submit(chunk_path, **kwargs):
        submit_attempts.append(Path(chunk_path).name)
        if Path(chunk_path).name == "chunk-a.pdf" and submit_attempts.count("chunk-a.pdf") == 1:
            raise RuntimeError("temporary OCR failure")
        return f"job-{Path(chunk_path).stem}"

    def fake_wait(job_id, **kwargs):
        return {"resultUrl": {"jsonUrl": f"https://example.com/{job_id}.jsonl"}}

    def fake_download(result_url, **kwargs):
        return [{"result": {"layoutParsingResults": [{"markdown": {"text": result_url, "images": {}}}]}}]

    monkeypatch.setattr("openkb.ocr.client.submit_ocr_job", fake_submit)
    monkeypatch.setattr("openkb.ocr.client.wait_for_ocr_job", fake_wait)
    monkeypatch.setattr("openkb.ocr.client.download_ocr_jsonl", fake_download)

    class FakeJob:
        def __init__(self):
            self.progress_updates: list[tuple[int, int]] = []
            self.logs: list[tuple[str, str]] = []

        def set_progress(self, current, total):
            self.progress_updates.append((current, total))

        def add_log(self, message, level="info"):
            self.logs.append((level, message))

        def raise_if_stopped(self):
            return None

    job = FakeJob()

    results = run_ocr_chunks(
        [chunk_a, chunk_b],
        token="token-abc",
        model="PaddleOCR-VL-1.5",
        max_retries=2,
        job=job,
    )

    assert len(results) == 2
    assert results[0]["chunk_index"] == 1
    assert results[1]["chunk_index"] == 2
    assert submit_attempts == ["chunk-a.pdf", "chunk-a.pdf", "chunk-b.pdf"]
    assert job.progress_updates == [(0, 2), (1, 2), (2, 2)]
    assert any(level == "warning" and "Retrying OCR chunk 1/2" in message for level, message in job.logs)


def test_run_ocr_chunks_logs_submission_polling_and_download_to_job_details(tmp_path, monkeypatch):
    chunk = tmp_path / "chunk-1.pdf"
    chunk.write_bytes(b"%PDF-1.4 fake")

    def fake_submit(chunk_path, **kwargs):
        return "job-chunk-1"

    def fake_wait(job_id, **kwargs):
        assert kwargs.get("progress_callback") is not None
        kwargs["progress_callback"](12, 100)
        kwargs["progress_callback"](100, 100)
        return {"resultUrl": {"jsonUrl": "https://example.com/job-chunk-1.jsonl"}}

    def fake_download(result_url, **kwargs):
        return [{"result": {"layoutParsingResults": [{"markdown": {"text": "OCR page", "images": {}}}]}}]

    monkeypatch.setattr("openkb.ocr.client.submit_ocr_job", fake_submit)
    monkeypatch.setattr("openkb.ocr.client.wait_for_ocr_job", fake_wait)
    monkeypatch.setattr("openkb.ocr.client.download_ocr_jsonl", fake_download)

    class FakeJob:
        def __init__(self):
            self.progress_updates: list[tuple[int, int]] = []
            self.logs: list[tuple[str, str]] = []

        def set_progress(self, current, total):
            self.progress_updates.append((current, total))

        def add_log(self, message, level="info"):
            self.logs.append((level, message))

        def raise_if_stopped(self):
            return None

    job = FakeJob()

    run_ocr_chunks(
        [chunk],
        token="token-abc",
        model="PaddleOCR-VL-1.5",
        job=job,
    )

    messages = [message for _level, message in job.logs]
    assert any("PaddleOCR job submitted: job-chunk-1" in message for message in messages)
    assert any("OCR chunk 1/1 progress: 12/100 page(s)" in message for message in messages)
    assert any("OCR chunk 1/1 progress: 100/100 page(s)" in message for message in messages)
    assert any("OCR chunk 1/1 result downloaded" in message for message in messages)
