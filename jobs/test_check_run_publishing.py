import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from github_integration.client import GitHubAppClient
from jobs.check_run_publishing import publish_check_run_payload
from jobs.models import AnalysisJob


@pytest.fixture
def completed_analysis():
    job_id = uuid.uuid4()
    payload = {
        "installation_id": 10,
        "repository_id": 20,
        "pull_request_number": 7,
        "head_sha": "abc123",
    }
    job = SimpleNamespace(
        job_type="analyze_pull_request",
        payload=payload,
        status=AnalysisJob.Status.COMPLETED,
    )
    result = SimpleNamespace(
        job=job,
        valid=True,
        findings=[{"rule_id": "test.rule", "severity": "low"}],
        diagnostics=[],
    )
    return job_id, payload, result


def test_publisher_uses_configured_github_client(completed_analysis):
    job_id, metadata, result = completed_analysis
    client = Mock()
    publisher = Mock()
    publisher.__enter__ = Mock(return_value=publisher)
    publisher.__exit__ = Mock(return_value=False)

    with (
        patch(
            "jobs.check_run_publishing.WorkflowAnalysisResult.objects.select_related"
        ) as select_related,
        patch.object(GitHubAppClient, "from_settings", return_value=client) as factory,
        patch(
            "jobs.check_run_publishing.CheckRunPublisher",
            return_value=publisher,
        ) as publisher_class,
    ):
        select_related.return_value.get.return_value = result

        publish_check_run_payload(
            {
                **metadata,
                "analysis_job_id": str(job_id),
            }
        )

    select_related.assert_called_once_with("job")
    select_related.return_value.get.assert_called_once_with(job_id=job_id)
    factory.assert_called_once_with()
    publisher_class.assert_called_once_with(client)
    publisher.publish.assert_called_once_with(
        installation_id=10,
        repository_id=20,
        pull_request_number=7,
        head_sha="abc123",
        analysis_job_id=str(job_id),
        valid=True,
        findings=result.findings,
        diagnostics=result.diagnostics,
    )


def test_publisher_rejects_invalid_analysis_job_uuid():
    with pytest.raises(ValueError, match="valid 'analysis_job_id'"):
        publish_check_run_payload(
            {
                "installation_id": 10,
                "repository_id": 20,
                "pull_request_number": 7,
                "head_sha": "abc123",
                "analysis_job_id": "not-a-uuid",
            }
        )


def test_publisher_rejects_missing_persisted_result(completed_analysis):
    job_id, metadata, _ = completed_analysis

    with (
        patch(
            "jobs.check_run_publishing.WorkflowAnalysisResult.objects.select_related"
        ) as select_related,
        patch(
            "jobs.check_run_publishing.GitHubAppClient.from_settings"
        ) as client_factory,
    ):
        select_related.return_value.get.side_effect = (
            # Use the model's actual DoesNotExist exception.
            __import__(
                "jobs.models", fromlist=["WorkflowAnalysisResult"]
            ).WorkflowAnalysisResult.DoesNotExist
        )

        with pytest.raises(ValueError, match="persisted analysis result"):
            publish_check_run_payload(
                {
                    **metadata,
                    "analysis_job_id": str(job_id),
                }
            )

    client_factory.assert_not_called()
