from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from jobs.management.commands.run_worker import (
    process_claimed_job,
    run_worker_loop,
)
from jobs.worker import ClaimedJob


def make_claim(job_type="test"):
    import uuid

    return ClaimedJob(
        job_id=uuid.uuid4(),
        job_type=job_type,
        payload={},
        attempt=1,
        lease_token=uuid.uuid4(),
    )


def test_run_worker_rejects_nonpositive_poll_interval():
    with pytest.raises(CommandError, match="poll-interval"):
        call_command("run_worker", poll_interval=0)


def test_run_worker_rejects_nonpositive_lease_duration():
    with pytest.raises(CommandError, match="lease-seconds"):
        call_command("run_worker", lease_seconds=0)


@patch("jobs.management.commands.run_worker.recover_exhausted_jobs")
@patch("jobs.management.commands.run_worker.claim_next_job")
def test_once_recovers_exhausted_jobs_and_exits_when_queue_is_empty(
    mock_claim,
    mock_recover,
):
    from threading import Event

    mock_claim.return_value = None

    run_worker_loop(
        stop_event=Event(),
        poll_interval=0.01,
        lease_seconds=120,
        once=True,
    )

    mock_recover.assert_called_once_with()
    mock_claim.assert_called_once_with(lease_seconds=120)


@patch("jobs.management.commands.run_worker.fail_job")
def test_unknown_job_type_fails_without_marking_it_completed(mock_fail):
    claim = make_claim(job_type="unsupported")

    with patch("jobs.management.commands.run_worker.complete_job") as mock_complete:
        process_claimed_job(claim)

    mock_fail.assert_called_once_with(
        claim,
        "No handler registered for job type: unsupported",
        retryable=False,
    )
    mock_complete.assert_not_called()


@patch("jobs.management.commands.run_worker.complete_job")
@patch("jobs.management.commands.run_worker.fail_job")
def test_handler_exception_is_recorded_as_retryable_failure(
    mock_fail,
    mock_complete,
):
    claim = make_claim(job_type="test")

    with (
        patch.dict(
            "jobs.management.commands.run_worker.JOB_HANDLERS",
            {"test": lambda _claim: (_ for _ in ()).throw(RuntimeError("boom"))},
        ),
        patch("jobs.management.commands.run_worker.logger.exception"),
    ):
        process_claimed_job(claim)

    mock_fail.assert_called_once()
    assert mock_fail.call_args.args[0] == claim
    assert mock_fail.call_args.args[1] == "boom"
    assert mock_fail.call_args.kwargs == {"retryable": True}
    mock_complete.assert_not_called()


@patch("jobs.management.commands.run_worker.complete_job", return_value=True)
@patch("jobs.management.commands.run_worker.fail_job")
def test_successful_handler_completes_job(mock_fail, mock_complete):
    claim = make_claim(job_type="test")

    with patch.dict(
        "jobs.management.commands.run_worker.JOB_HANDLERS",
        {"test": lambda _claim: None},
    ):
        process_claimed_job(claim)

    mock_complete.assert_called_once_with(claim)
    mock_fail.assert_not_called()
