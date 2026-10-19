import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.test import override_settings

from github_integration.models import GitHubInstallation, Repository
from jobs.models import AnalysisJob, WorkflowAnalysisResult
from jobs.worker import claim_next_job

WEBHOOK_SECRET = "test-webhook-secret"
INSTALLATION_ID = 123
REPOSITORY_ID = 456
PULL_REQUEST_NUMBER = 7
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40

WORKFLOW_BEFORE = """
name: CI
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

WORKFLOW_AFTER = """
name: CI
on: push
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def signed_headers(body, *, delivery_id="delivery-lifecycle", event="pull_request"):
    digest = hmac.new(
        WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return {
        "HTTP_X_HUB_SIGNATURE_256": f"sha256={digest}",
        "HTTP_X_GITHUB_EVENT": event,
        "HTTP_X_GITHUB_DELIVERY": delivery_id,
        "content_type": "application/json",
    }


def github_file(path, content):
    return {
        "path": path,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    }


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=WEBHOOK_SECRET)
def test_pull_request_webhook_runs_analysis_and_enqueues_publication(client):
    installation = GitHubInstallation.objects.create(
        installation_id=INSTALLATION_ID,
        account_login="example",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
        status=GitHubInstallation.Status.ACTIVE,
    )
    repository = Repository.objects.create(
        installation=installation,
        github_repo_id=REPOSITORY_ID,
        full_name="example/project",
        default_branch="main",
        status=Repository.Status.ACTIVE,
    )

    payload = {
        "action": "opened",
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": REPOSITORY_ID},
        "number": PULL_REQUEST_NUMBER,
        "pull_request": {
            "base": {"sha": BASE_SHA},
            "head": {"sha": HEAD_SHA},
        },
    }
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(body),
    )
    assert response.status_code == 202, response.content.decode()

    analysis_job = AnalysisJob.objects.get(job_type="analyze_pull_request")
    assert analysis_job.payload["repository_id"] == REPOSITORY_ID
    assert analysis_job.payload["pull_request_number"] == PULL_REQUEST_NUMBER
    assert analysis_job.payload["base_sha"] == BASE_SHA
    assert analysis_job.payload["head_sha"] == HEAD_SHA

    from jobs.management.commands.run_worker import process_claimed_job

    analysis_claim = claim_next_job()
    assert analysis_claim is not None
    assert analysis_claim.job_id == analysis_job.id

    workflow_path = ".github/workflows/ci.yml"
    api_responses = [
        {
            "id": REPOSITORY_ID,
            "full_name": "example/project",
        },
        [
            {
                "filename": workflow_path,
                "status": "modified",
            }
        ],
        github_file(workflow_path, WORKFLOW_BEFORE),
        github_file(workflow_path, WORKFLOW_AFTER),
    ]

    with (
        patch(
            "github_integration.workflow_revisions._repository_for_job",
            return_value=repository,
        ),
        patch(
            "github_integration.workflow_revisions.GitHubAppClient.from_settings"
        ) as client_factory,
    ):
        github_client = client_factory.return_value
        github_client.request.side_effect = api_responses
        github_client.close.return_value = None

        process_claimed_job(analysis_claim)

    analysis_job.refresh_from_db()
    assert analysis_job.status == AnalysisJob.Status.COMPLETED

    result = WorkflowAnalysisResult.objects.get(job=analysis_job)
    assert result.valid is True
    assert any(
        finding.get("rule_id") == "permissions.write_scope_added"
        for finding in result.findings
    )

    publication_job = AnalysisJob.objects.get(job_type="publish_check_run")
    assert publication_job.payload["analysis_job_id"] == str(analysis_job.id)
    assert publication_job.payload["head_sha"] == HEAD_SHA

    publication_claim = claim_next_job()
    assert publication_claim is not None
    assert publication_claim.job_id == publication_job.id

    with (
        patch(
            "jobs.check_run_publishing.GitHubAppClient.from_settings"
        ) as publisher_client_factory,
        patch("jobs.check_run_publishing.CheckRunPublisher") as publisher_class,
    ):
        publisher_client = publisher_client_factory.return_value
        publisher = publisher_class.return_value.__enter__.return_value

        process_claimed_job(publication_claim)

    publisher_client_factory.assert_called_once_with()
    publisher_class.assert_called_once_with(publisher_client)
    publisher.publish.assert_called_once()

    published = publisher.publish.call_args.kwargs
    assert published["head_sha"] == HEAD_SHA
    assert published["valid"] is True
    assert any(
        finding.get("rule_id") == "permissions.write_scope_added"
        for finding in published["findings"]
    )

    publication_job.refresh_from_db()
    assert publication_job.status == AnalysisJob.Status.COMPLETED
