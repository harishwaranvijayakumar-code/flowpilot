from django.db import transaction
from django.utils import timezone

from jobs.analysis_handler import WorkflowAnalysisOutput
from jobs.models import AnalysisJob, WorkflowAnalysisResult
from jobs.worker import ClaimedJob


def persist_workflow_result_and_complete(
    claim: ClaimedJob,
    output: WorkflowAnalysisOutput,
) -> bool:
    """Save an analysis result and complete its job under a valid lease."""
    now = timezone.now()

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

        WorkflowAnalysisResult.objects.update_or_create(
            job=job,
            defaults={
                "schema_version": output.schema_version,
                "valid": output.valid,
                "findings": output.findings,
                "diagnostics": output.diagnostics,
            },
        )

        job.status = AnalysisJob.Status.COMPLETED
        job.lease_until = None
        job.lease_token = None
        job.save(
            update_fields=[
                "status",
                "lease_until",
                "lease_token",
                "updated_at",
            ]
        )

        return True
