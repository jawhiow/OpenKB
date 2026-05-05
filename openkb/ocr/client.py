from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import requests


OCR_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


def _job_log(job, message: str, level: str = "info") -> None:
    add_log = getattr(job, "add_log", None)
    if callable(add_log):
        add_log(message, level=level)


def _job_set_progress(job, current: int, total: int) -> None:
    set_progress = getattr(job, "set_progress", None)
    if callable(set_progress):
        set_progress(current, total)


def _job_checkpoint(job) -> None:
    raise_if_stopped = getattr(job, "raise_if_stopped", None)
    if callable(raise_if_stopped):
        raise_if_stopped()


def submit_ocr_job(
    file_path: Path,
    *,
    token: str,
    model: str,
    optional_payload: dict[str, Any] | None = None,
    requests_module=requests,
) -> str:
    """Submit one local PDF chunk to the PaddleOCR async API and return its job id."""
    headers = {"Authorization": f"bearer {token}"}
    data: dict[str, Any] = {"model": model}
    if optional_payload is not None:
        data["optionalPayload"] = json.dumps(optional_payload)
    with Path(file_path).open("rb") as fh:
        response = requests_module.post(OCR_JOB_URL, headers=headers, data=data, files={"file": fh})
    if response.status_code != 200:
        raise RuntimeError(f"Failed to submit OCR job: {response.text}")
    return response.json()["data"]["jobId"]


def wait_for_ocr_job(
    job_id: str,
    *,
    token: str,
    requests_module=requests,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval: float = 5.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Poll one PaddleOCR job until completion and return the final payload."""
    headers = {"Authorization": f"bearer {token}"}
    while True:
        response = requests_module.get(f"{OCR_JOB_URL}/{job_id}", headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to poll OCR job {job_id}: {response.text}")
        payload = response.json()["data"]
        state = payload["state"]
        progress = payload.get("extractProgress") or {}
        total_pages = int(progress.get("totalPages") or 0)
        extracted_pages = int(progress.get("extractedPages") or 0)
        if state == "running" and progress_callback and total_pages:
            progress_callback(extracted_pages, total_pages)
        if state == "done":
            if progress_callback and total_pages:
                progress_callback(extracted_pages or total_pages, total_pages)
            return payload
        if state == "failed":
            raise RuntimeError(payload.get("errorMsg") or f"OCR job {job_id} failed")
        sleep_fn(poll_interval)


def download_ocr_jsonl(result_url: str, *, requests_module=requests) -> list[dict[str, Any]]:
    """Download and parse one PaddleOCR JSONL result into a list of JSON payloads."""
    response = requests_module.get(result_url)
    response.raise_for_status()
    payloads: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        payloads.append(json.loads(line))
    return payloads


def run_ocr_chunks(
    chunk_files: list[Path],
    *,
    token: str,
    model: str,
    optional_payload: dict[str, Any] | None = None,
    max_retries: int = 2,
    requests_module=requests,
    sleep_fn: Callable[[float], None] = time.sleep,
    job=None,
) -> list[dict[str, Any]]:
    """Run OCR sequentially over prepared PDF chunks with per-chunk retries."""
    total = len(chunk_files)
    if job is not None:
        _job_set_progress(job, 0, total)

    results: list[dict[str, Any]] = []
    for chunk_index, chunk_path in enumerate(chunk_files, start=1):
        attempts = 0
        while True:
            if job is not None:
                _job_checkpoint(job)
            attempts += 1
            try:
                if job is not None:
                    _job_log(job, f"OCR chunk {chunk_index}/{total}: {Path(chunk_path).name} (attempt {attempts})")
                job_id = submit_ocr_job(
                    chunk_path,
                    token=token,
                    model=model,
                    optional_payload=optional_payload,
                    requests_module=requests_module,
                )
                if job is not None:
                    _job_log(job, f"PaddleOCR job submitted: {job_id}")

                def report_progress(current: int, page_total: int) -> None:
                    if job is not None:
                        _job_log(
                            job,
                            f"OCR chunk {chunk_index}/{total} progress: {current}/{page_total} page(s)",
                        )

                final_payload = wait_for_ocr_job(
                    job_id,
                    token=token,
                    requests_module=requests_module,
                    sleep_fn=sleep_fn,
                    progress_callback=report_progress if job is not None else None,
                )
                json_url = final_payload["resultUrl"]["jsonUrl"]
                payloads = download_ocr_jsonl(json_url, requests_module=requests_module)
                if job is not None:
                    _job_log(job, f"OCR chunk {chunk_index}/{total} result downloaded")
                results.append(
                    {
                        "chunk_index": chunk_index,
                        "chunk_path": str(chunk_path),
                        "job_id": job_id,
                        "result_url": json_url,
                        "payloads": payloads,
                    }
                )
                if job is not None:
                    _job_set_progress(job, chunk_index, total)
                break
            except Exception as exc:
                if attempts >= max_retries:
                    raise
                if job is not None:
                    _job_log(
                        job,
                        f"Retrying OCR chunk {chunk_index}/{total} after error: {exc}",
                        level="warning",
                    )
    return results
