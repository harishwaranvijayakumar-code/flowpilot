import uuid
from typing import Any

from django.core.exceptions import ValidationError

from github_integration.check_runs import CheckRunPublisher
from github_integration.client import GitHubAppClient
from jobs.models import AnalysisJob, WorkflowAnalysisResult


def _positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Check Run payload requires a positive integer '{field}'.")
    return value


def publish_check_run_payload(payload: dict[str, Any]) -> None:
    """Publish a persisted PR analysis result as a GitHub Check Run."""
    if not isinstance(payload, dict):
        raise ValueError("Check Run payload must be an object.")

    installation_id = _positive_int(payload, "installation_id")
    repository_id = _positive_int(payload, "repository_id")
    pull_request_number = _positive_int(payload, "pull_request_number")
    head_sha = payload.get("head_sha")
    analysis_job_id = payload.get("analysis_job_id")

    if (
        not isinstance(head_sha, str)
        or not head_sha
        or len(head_sha) > 128
        or head_sha.strip() != head_sha
    ):
        raise ValueError("Check Run payload requires a valid 'head_sha'.")

    if not isinstance(analysis_job_id, str):
        raise ValueError("Check Run payload requires an 'analysis_job_id'.")

    try:
        parsed_job_id = uuid.UUID(analysis_job_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "Check Run payload requires a valid 'analysis_job_id'."
        ) from exc

    try:
        result = WorkflowAnalysisResult.objects.select_related("job").get(
            job_id=parsed_job_id
        )
    except (
        WorkflowAnalysisResult.DoesNotExist,
        ValidationError,
        ValueError,
    ) as exc:
        raise ValueError(
            "The persisted analysis result for this Check Run is unavailable."
        ) from exc

    job = result.job
    job_payload = job.payload

    if (
        job.job_type != "analyze_pull_request"
        or job_payload.get("installation_id") != installation_id
        or job_payload.get("repository_id") != repository_id
        or job_payload.get("pull_request_number") != pull_request_number
        or job_payload.get("head_sha") != head_sha
        or job.status != AnalysisJob.Status.COMPLETED
    ):
        raise ValueError(
            "Check Run payload does not match a completed pull-request analysis."
        )

    client = GitHubAppClient.from_settings()
    with CheckRunPublisher(client) as publisher:
        publisher.publish(
            installation_id=installation_id,
            repository_id=repository_id,
            pull_request_number=pull_request_number,
            head_sha=head_sha,
            analysis_job_id=str(parsed_job_id),
            valid=result.valid,
            findings=result.findings,
            diagnostics=result.diagnostics,
        )
