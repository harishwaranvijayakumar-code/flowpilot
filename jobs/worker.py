import random
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from jobs.models import AnalysisJob

DEFAULT_LEASE_SECONDS = 120
MAX_BACKOFF_SECONDS = 300


@dataclass(frozen=True)
class ClaimedJob:
    job_id: uuid.UUID
    job_type: str
    payload: dict
    attempt: int
    lease_token: uuid.UUID


def claim_next_job(lease_seconds=DEFAULT_LEASE_SECONDS):
    now = timezone.now()

    with transaction.atomic():
        job = (
            AnalysisJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(
                    status__in=[
                        AnalysisJob.Status.QUEUED,
                        AnalysisJob.Status.RETRY_WAIT,
                    ],
                    available_at__lte=now,
                )
                | Q(
                    status=AnalysisJob.Status.RUNNING,
                    lease_until__lte=now,
                ),
                attempts__lt=F("max_attempts"),
            )
            .order_by("available_at", "created_at")
            .first()
        )

        if job is None:
            return None

        token = uuid.uuid4()
        job.status = AnalysisJob.Status.RUNNING
        job.attempts += 1
        job.lease_token = token
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.last_error = ""
        job.save(
            update_fields=[
                "status",
                "attempts",
                "lease_token",
                "lease_until",
                "last_error",
                "updated_at",
            ]
        )

        return ClaimedJob(
            job_id=job.id,
            job_type=job.job_type,
            payload=job.payload,
            attempt=job.attempts,
            lease_token=token,
        )


def complete_job(claim: ClaimedJob) -> bool:
    updated = AnalysisJob.objects.filter(
        id=claim.job_id,
        status=AnalysisJob.Status.RUNNING,
        lease_token=claim.lease_token,
        lease_until__gt=timezone.now(),
    ).update(
        status=AnalysisJob.Status.COMPLETED,
        lease_until=None,
        lease_token=None,
        updated_at=timezone.now(),
    )
    return updated == 1


def fail_job(claim: ClaimedJob, error: str, retryable=True) -> bool:
    now = timezone.now()
    error = error[:4000]

    with transaction.atomic():
        job = (
            AnalysisJob.objects.select_for_update()
            .filter(
                id=claim.job_id,
                status=AnalysisJob.Status.RUNNING,
                lease_token=claim.lease_token,
                lease_until__gt=now,
            )
            .first()
        )

        if job is None:
            return False

        job.last_error = error
        job.lease_until = None
        job.lease_token = None

        if retryable and job.attempts < job.max_attempts:
            delay = min(
                2 ** max(job.attempts - 1, 0),
                MAX_BACKOFF_SECONDS,
            )
            delay += random.uniform(0, 1)
            job.status = AnalysisJob.Status.RETRY_WAIT
            job.available_at = now + timedelta(seconds=delay)
        else:
            job.status = AnalysisJob.Status.FAILED

        job.save(
            update_fields=[
                "status",
                "available_at",
                "lease_until",
                "lease_token",
                "last_error",
                "updated_at",
            ]
        )
        return True


def recover_exhausted_jobs():
    """Mark expired claims terminal when no attempts remain."""
    now = timezone.now()

    return AnalysisJob.objects.filter(
        status=AnalysisJob.Status.RUNNING,
        lease_until__lte=now,
        attempts__gte=F("max_attempts"),
    ).update(
        status=AnalysisJob.Status.FAILED,
        lease_until=None,
        lease_token=None,
        last_error="Worker lease expired after the final attempt.",
        updated_at=now,
    )
