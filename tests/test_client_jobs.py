from __future__ import annotations

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
