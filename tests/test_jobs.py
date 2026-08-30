"""Jobbplatsen: en åt gången, och fel ska nå gränssnittet i stället för att tystna."""

import threading

import pytest

from icakort import jobs


def test_runs_work_and_keeps_result():
    runner = jobs.JobRunner()
    job = runner.start("sync", lambda j: {"fetched": 3})
    runner.join(5)

    assert job.state == "done"
    assert job.result == {"fetched": 3}
    assert job.finished_at is not None


def test_only_one_job_at_a_time():
    runner = jobs.JobRunner()
    release = threading.Event()
    runner.start("login", lambda j: release.wait(5) and None)

    with pytest.raises(jobs.JobBusy):
        runner.start("sync", lambda j: None)

    release.set()
    runner.join(5)
    # När den första är klar går det att starta en ny.
    runner.start("sync", lambda j: None)
    runner.join(5)


def test_failure_is_captured_not_swallowed():
    runner = jobs.JobRunner()

    def boom(job):
        raise RuntimeError("Kivra svarade 401")

    job = runner.start("sync", boom)
    runner.join(5)

    assert job.state == "error"
    assert job.error == "Kivra svarade 401"
    assert any("401" in line for line in job.log)


def test_qr_is_cleared_when_the_flow_ends():
    """En QR-kod efter avslutad signering är bara vilseledande."""
    runner = jobs.JobRunner()

    def sign(job):
        job.qr_payload = "bankid.abc"
        return None

    job = runner.start("login", sign)
    runner.join(5)
    assert job.qr_payload is None
    assert job.to_dict()["has_qr"] is False


def test_log_is_capped():
    job = jobs.Job(kind="sync")
    for i in range(jobs.MAX_LOG_LINES + 50):
        jobs.log(job, f"rad {i}")

    assert len(job.log) == jobs.MAX_LOG_LINES
    assert job.log[-1] == f"rad {jobs.MAX_LOG_LINES + 49}"   # de senaste behålls
