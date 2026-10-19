from django.db import transaction
from django.utils import timezone

from jobs.analysis_handler import WorkflowAnalysisOutput
from jobs.models import AnalysisJob, WorkflowAnalysisResult
from jobs.worker import ClaimedJob


def persist_workflow_result_and_complete(
    claim: ClaimedJob,
    output: WorkflowAnalysisOutput,
) -> bool:
    """Persist results and enqueue publication under a valid job lease."""
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

        if job.job_type == "analyze_pull_request":
            payload = job.payload
            installation_id = payload.get("installation_id")
            repository_id = payload.get("repository_id")
            pull_request_number = payload.get("pull_request_number")
            head_sha = payload.get("head_sha")

            if (
                isinstance(installation_id, bool)
                or not isinstance(installation_id, int)
                or installation_id <= 0
                or isinstance(repository_id, bool)
                or not isinstance(repository_id, int)
                or repository_id <= 0
                or isinstance(pull_request_number, bool)
                or not isinstance(pull_request_number, int)
                or pull_request_number <= 0
                or not isinstance(head_sha, str)
                or not head_sha
                or len(head_sha) > 128
            ):
                raise ValueError(
                    "Completed pull-request analysis has invalid Check Run metadata."
                )

            dedupe_key = f"pr-check:{repository_id}:{pull_request_number}:{head_sha}"

            AnalysisJob.objects.get_or_create(
                dedupe_key=dedupe_key,
                defaults={
                    "job_type": "publish_check_run",
                    "payload": {
                        "installation_id": installation_id,
                        "repository_id": repository_id,
                        "pull_request_number": pull_request_number,
                        "head_sha": head_sha,
                        "analysis_job_id": str(job.id),
                    },
                },
            )

        return True
