from __future__ import annotations

import threading

from openkb.client.jobs import JobRegistry


def test_job_registry_records_successful_job_result():
    registry = JobRegistry()

    job = registry.submit("query", lambda current: {"answer": "hello"})
    completed = registry.wait(job.id, timeout=2)

    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.result == {"answer": "hello"}
    assert completed.error is None
    assert completed.logs[-1]["message"] == "Job succeeded"


def test_job_registry_records_failure_message():
    registry = JobRegistry()

    def fail(_job):
        raise RuntimeError("boom")

    job = registry.submit("lint", fail)
    completed = registry.wait(job.id, timeout=2)

    assert completed is not None
    assert completed.status == "failed"
    assert completed.result is None
    assert completed.error == "boom"
    assert completed.logs[-1]["message"] == "boom"


def test_job_registry_lists_recent_jobs_newest_first():
    registry = JobRegistry()

    first = registry.submit("first", lambda current: "one")
    second = registry.submit("second", lambda current: "two")
    registry.wait(first.id, timeout=2)
    registry.wait(second.id, timeout=2)

    jobs = registry.list_jobs()

    assert [job.id for job in jobs] == [second.id, first.id]


def test_job_progress_and_logs_are_serialized():
    registry = JobRegistry()

    def run(job):
        job.set_progress(1, 3)
        job.add_log("Processing first file")
        job.set_progress(2, 3)
        job.add_log("Processing second file")
        return "ok"

    job = registry.submit("add", run, message="Queued")
    completed = registry.wait(job.id, timeout=2)

    assert completed is not None
    data = completed.to_dict()
    assert data["progress"] == {"current": 2, "total": 3}
    assert [entry["message"] for entry in data["logs"][-3:]] == [
        "Processing first file",
        "Processing second file",
        "Job succeeded",
    ]


def test_job_registry_can_stop_running_job():
    registry = JobRegistry()
    started = threading.Event()
    release = threading.Event()

    def run(job):
        started.set()
        release.wait(timeout=2)
        job.raise_if_stopped()
        return "done"

    job = registry.submit("add", run, message="Queued")
    assert started.wait(timeout=2)

    stopped = registry.stop(job.id)
    release.set()
    completed = registry.wait(job.id, timeout=2)

    assert stopped is not None
    assert completed is not None
    assert completed.status == "stopped"
    assert completed.stop_requested is True
    assert completed.error is None
    assert any(entry["message"] == "Stop requested" for entry in completed.logs)
    assert completed.to_dict()["stop_requested"] is True


def test_job_registry_can_retry_failed_job():
    registry = JobRegistry()
    attempts: list[str] = []

    def run(job):
        attempts.append(job.id)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        return {"ok": True}

    job = registry.submit("add", run, message="Queued")
    failed = registry.wait(job.id, timeout=2)
    retry = registry.retry(job.id)
    completed = registry.wait(retry.id, timeout=2)

    assert failed is not None
    assert failed.status == "failed"
    assert retry.id != job.id
    assert retry.type == "add"
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.result == {"ok": True}
    assert attempts == [job.id, retry.id]
