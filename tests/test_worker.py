from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, connections
from django.utils import timezone

from jobs.models import AnalysisJob
from jobs.worker import (
    claim_next_job,
    complete_job,
    fail_job,
    recover_exhausted_jobs,
)


@pytest.mark.django_db(transaction=True)
def test_concurrent_workers_cannot_claim_the_same_job():
    AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-concurrent-single-job",
    )

    barrier = Barrier(2)

    def attempt_claim():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            claim = claim_next_job()
            return claim
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_claim),
            executor.submit(attempt_claim),
        ]
        claims = [future.result(timeout=20) for future in futures]

    successful_claims = [claim for claim in claims if claim is not None]

    assert len(successful_claims) == 1
    assert AnalysisJob.objects.get().status == AnalysisJob.Status.RUNNING


@pytest.mark.django_db(transaction=True)
def test_concurrent_workers_claim_distinct_jobs():
    AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-concurrent-job-a",
    )
    AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-concurrent-job-b",
    )

    barrier = Barrier(2)

    def attempt_claim():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return claim_next_job()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_claim),
            executor.submit(attempt_claim),
        ]
        claims = [future.result(timeout=20) for future in futures]

    assert all(claim is not None for claim in claims)
    assert len({claim.job_id for claim in claims}) == 2


@pytest.mark.django_db
def test_claim_assigns_lease_and_increments_attempt():
    job = AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-claim-test",
    )

    claim = claim_next_job()

    assert claim is not None
    assert claim.job_id == job.id
    assert claim.attempt == 1

    job.refresh_from_db()
    assert job.status == AnalysisJob.Status.RUNNING
    assert job.lease_token == claim.lease_token
    assert job.lease_until is not None


@pytest.mark.django_db
def test_completion_clears_lease():
    AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-complete-test",
    )
    claim = claim_next_job()

    assert claim is not None
    assert complete_job(claim) is True

    job = AnalysisJob.objects.get(id=claim.job_id)
    assert job.status == AnalysisJob.Status.COMPLETED
    assert job.lease_token is None
    assert job.lease_until is None


@pytest.mark.django_db
def test_stale_claim_cannot_complete_reclaimed_job():
    job = AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-stale-claim-test",
    )
    first_claim = claim_next_job()
    assert first_claim is not None

    AnalysisJob.objects.filter(id=job.id).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )

    second_claim = claim_next_job()
    assert second_claim is not None
    assert second_claim.lease_token != first_claim.lease_token

    assert complete_job(first_claim) is False
    assert complete_job(second_claim) is True


@pytest.mark.django_db
def test_retryable_failure_schedules_retry():
    AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-retry-test",
    )
    claim = claim_next_job()
    assert claim is not None

    assert fail_job(claim, "temporary error", retryable=True) is True

    job = AnalysisJob.objects.get(id=claim.job_id)
    assert job.status == AnalysisJob.Status.RETRY_WAIT
    assert job.available_at > timezone.now()
    assert job.lease_token is None


@pytest.mark.django_db
def test_non_retryable_failure_is_terminal():
    AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-terminal-test",
    )
    claim = claim_next_job()
    assert claim is not None

    assert fail_job(claim, "permanent error", retryable=False) is True

    job = AnalysisJob.objects.get(id=claim.job_id)
    assert job.status == AnalysisJob.Status.FAILED
    assert job.lease_token is None


@pytest.mark.django_db
def test_exhausted_expired_lease_is_failed():
    job = AnalysisJob.objects.create(
        job_type="test",
        dedupe_key="worker-exhausted-test",
        attempts=5,
        max_attempts=5,
        status=AnalysisJob.Status.RUNNING,
        lease_until=timezone.now() - timedelta(seconds=1),
        lease_token="f1d6a89d-09c4-4b0d-90f2-8f49cb3b5c27",
    )

    changed = recover_exhausted_jobs()

    assert changed == 1
    job.refresh_from_db()
    assert job.status == AnalysisJob.Status.FAILED
    assert job.lease_token is None
