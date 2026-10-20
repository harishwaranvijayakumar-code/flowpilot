import hashlib
import hmac
import json

import pytest
from django.test import Client
from django.test.utils import override_settings

from github_integration.models import GitHubInstallation, Repository
from jobs.models import AnalysisJob
from webhooks.models import WebhookDelivery

SECRET = "test-webhook-secret"


def signed_headers(body, delivery_id="delivery-001", event="pull_request"):
    signature = hmac.new(
        SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return {
        "HTTP_X_HUB_SIGNATURE_256": f"sha256={signature}",
        "HTTP_X_GITHUB_DELIVERY": delivery_id,
        "HTTP_X_GITHUB_EVENT": event,
        "content_type": "application/json",
    }


def pull_request_payload(action="opened"):
    return {
        "action": action,
        "installation": {"id": 12345},
        "repository": {"id": 67890},
        "number": 7,
        "pull_request": {
            "base": {"sha": "base-sha-001"},
            "head": {"sha": "head-sha-001"},
        },
    }


def installation_payload(action, installation_id=12345):
    return {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {
                "login": "flowpilot-test",
                "type": "Organization",
            },
        },
    }


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def installation(db):
    return GitHubInstallation.objects.create(
        installation_id=12345,
        account_login="flowpilot-test",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
    )


@pytest.fixture
def repository(installation):
    return Repository.objects.create(
        installation=installation,
        github_repo_id=67890,
        full_name="flowpilot-test/example",
        default_branch="main",
        html_url="https://github.com/flowpilot-test/example",
        is_private=True,
    )


def post_installation_event(
    client,
    action,
    delivery_id,
    installation_id=12345,
):
    body = json.dumps(
        installation_payload(action, installation_id),
    ).encode()

    return client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(
            body,
            delivery_id=delivery_id,
            event="installation",
        ),
    )


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_valid_pull_request_webhook_creates_delivery_and_job(
    client,
    installation,
):
    body = json.dumps(pull_request_payload()).encode()
    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(body),
    )

    assert response.status_code == 202
    assert WebhookDelivery.objects.count() == 1
    assert AnalysisJob.objects.count() == 1

    job = AnalysisJob.objects.get()
    assert job.status == AnalysisJob.Status.QUEUED
    assert job.job_type == "analyze_pull_request"
    assert job.payload["repository_id"] == 67890
    assert job.payload["pull_request_number"] == 7
    assert job.payload["head_sha"] == "head-sha-001"


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_invalid_signature_is_rejected(client):
    body = json.dumps(pull_request_payload()).encode()
    headers = signed_headers(body)
    headers["HTTP_X_HUB_SIGNATURE_256"] = "sha256=invalid"

    response = client.post(
        "/webhooks/github/",
        data=body,
        **headers,
    )

    assert response.status_code == 401
    assert WebhookDelivery.objects.count() == 0
    assert AnalysisJob.objects.count() == 0


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_duplicate_pull_request_delivery_does_not_duplicate_job(
    client,
    installation,
):
    body = json.dumps(pull_request_payload()).encode()
    headers = signed_headers(body)

    first = client.post("/webhooks/github/", data=body, **headers)
    second = client.post("/webhooks/github/", data=body, **headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert WebhookDelivery.objects.count() == 1
    assert AnalysisJob.objects.count() == 1


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_unsupported_event_is_recorded_as_ignored(client):
    body = json.dumps({"action": "completed"}).encode()
    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(body, event="ping"),
    )

    assert response.status_code == 202
    assert WebhookDelivery.objects.get().status == WebhookDelivery.Status.IGNORED
    assert AnalysisJob.objects.count() == 0


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_malformed_json_is_rejected(client):
    body = b'{"action":'
    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(body),
    )

    assert response.status_code == 400
    assert WebhookDelivery.objects.count() == 0


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET="")
def test_missing_webhook_secret_disables_endpoint(client):
    body = b"{}"
    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(body),
    )

    assert response.status_code == 503
    assert WebhookDelivery.objects.count() == 0


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_unsupported_pull_request_action_creates_no_job(client):
    body = json.dumps(pull_request_payload(action="closed")).encode()
    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(body, delivery_id="delivery-closed"),
    )

    assert response.status_code == 202
    assert WebhookDelivery.objects.get().status == WebhookDelivery.Status.IGNORED
    assert AnalysisJob.objects.count() == 0


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_installation_created_creates_record_and_reconciliation_job(client):
    response = post_installation_event(
        client,
        "created",
        "delivery-created",
        installation_id=54321,
    )

    assert response.status_code == 202

    installation = GitHubInstallation.objects.get(installation_id=54321)
    assert installation.account_login == "flowpilot-test"
    assert installation.account_type == GitHubInstallation.AccountType.ORGANIZATION
    assert installation.lifecycle_generation == 1

    job = AnalysisJob.objects.get()
    assert job.job_type == "reconcile_installation"
    assert job.payload == {
        "installation_id": 54321,
        "lifecycle_generation": 1,
    }
    assert job.status == AnalysisJob.Status.QUEUED


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_installation_created_refreshes_metadata_without_forcing_status(
    client,
    installation,
):
    installation.status = GitHubInstallation.Status.SUSPENDED
    installation.account_login = "old-login"
    installation.save()

    response = post_installation_event(
        client,
        "created",
        "delivery-created-existing",
    )

    assert response.status_code == 202

    installation.refresh_from_db()
    assert installation.status == GitHubInstallation.Status.SUSPENDED
    assert installation.account_login == "flowpilot-test"
    assert installation.lifecycle_generation == 1
    assert GitHubInstallation.objects.filter(installation_id=12345).count() == 1

    job = AnalysisJob.objects.get()
    assert job.job_type == "reconcile_installation"
    assert job.payload["lifecycle_generation"] == 1


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_installation_unsuspend_schedules_reconciliation(
    client,
    installation,
    repository,
):
    installation.status = GitHubInstallation.Status.SUSPENDED
    installation.save(update_fields=["status", "updated_at"])

    repository.status = Repository.Status.DISABLED
    repository.save(update_fields=["status", "updated_at"])

    response = post_installation_event(
        client,
        "unsuspend",
        "delivery-unsuspend",
    )

    assert response.status_code == 202

    installation.refresh_from_db()
    repository.refresh_from_db()

    # The reconciliation worker, not the webhook, applies authoritative state.
    assert installation.status == GitHubInstallation.Status.SUSPENDED
    assert repository.status == Repository.Status.DISABLED
    assert installation.lifecycle_generation == 1

    job = AnalysisJob.objects.get()
    assert job.job_type == "reconcile_installation"
    assert job.payload == {
        "installation_id": 12345,
        "lifecycle_generation": 1,
    }


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_installation_suspension_schedules_reconciliation(
    client,
    installation,
    repository,
):
    response = post_installation_event(
        client,
        "suspend",
        "delivery-suspend",
    )

    assert response.status_code == 202

    installation.refresh_from_db()
    repository.refresh_from_db()

    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert repository.status == Repository.Status.ACTIVE
    assert installation.lifecycle_generation == 1

    job = AnalysisJob.objects.get()
    assert job.job_type == "reconcile_installation"
    assert job.payload["lifecycle_generation"] == 1


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_installation_deleted_schedules_reconciliation(
    client,
    installation,
    repository,
):
    response = post_installation_event(
        client,
        "deleted",
        "delivery-deleted",
    )

    assert response.status_code == 202

    installation.refresh_from_db()
    repository.refresh_from_db()

    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert repository.status == Repository.Status.ACTIVE
    assert installation.lifecycle_generation == 1

    job = AnalysisJob.objects.get()
    assert job.job_type == "reconcile_installation"
    assert job.payload["lifecycle_generation"] == 1


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_duplicate_installation_delivery_does_not_advance_generation(client):
    body = json.dumps(installation_payload("created", 54321)).encode()
    headers = signed_headers(
        body,
        delivery_id="delivery-created-duplicate",
        event="installation",
    )

    first = client.post("/webhooks/github/", data=body, **headers)
    second = client.post("/webhooks/github/", data=body, **headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert WebhookDelivery.objects.count() == 1

    installation = GitHubInstallation.objects.get(installation_id=54321)
    assert installation.lifecycle_generation == 1

    jobs = AnalysisJob.objects.filter(job_type="reconcile_installation")
    assert jobs.count() == 1


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_separate_installation_events_get_distinct_generations_and_jobs(client):
    first = post_installation_event(
        client,
        "created",
        "delivery-created-first",
        installation_id=54321,
    )
    second = post_installation_event(
        client,
        "unsuspend",
        "delivery-created-second",
        installation_id=54321,
    )

    assert first.status_code == 202
    assert second.status_code == 202

    installation = GitHubInstallation.objects.get(installation_id=54321)
    assert installation.lifecycle_generation == 2

    jobs = AnalysisJob.objects.filter(job_type="reconcile_installation").order_by(
        "created_at",
    )
    assert jobs.count() == 2
    assert {job.payload["lifecycle_generation"] for job in jobs} == {1, 2}
    assert {job.dedupe_key for job in jobs} == {
        "installation-reconcile:54321:1",
        "installation-reconcile:54321:2",
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "account",
    [
        None,
        {},
        {"login": "", "type": "Organization"},
        {"login": "flowpilot", "type": "unknown"},
    ],
)
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_installation_created_rejects_invalid_account(client, account):
    payload = installation_payload("created", 54321)
    payload["installation"]["account"] = account
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(
            body,
            delivery_id="delivery-invalid-account",
            event="installation",
        ),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_installation_account"
    assert not GitHubInstallation.objects.filter(installation_id=54321).exists()
    assert WebhookDelivery.objects.count() == 0
    assert AnalysisJob.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["suspend", "unsuspend", "deleted"])
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_lifecycle_event_without_account_metadata_is_rejected(client, action):
    payload = {
        "action": action,
        "installation": {"id": 54321},
    }
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(
            body,
            delivery_id=f"delivery-{action}",
            event="installation",
        ),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_installation_account"
    assert not GitHubInstallation.objects.filter(installation_id=54321).exists()
    assert WebhookDelivery.objects.count() == 0
    assert AnalysisJob.objects.count() == 0


@pytest.mark.django_db
@override_settings(GITHUB_WEBHOOK_SECRET=SECRET)
def test_suspension_event_refreshes_metadata_but_does_not_set_status(
    client,
    installation,
):
    payload = installation_payload("suspend")
    payload["installation"]["account"]["login"] = "renamed-org"
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(
            body,
            delivery_id="delivery-suspend-metadata",
            event="installation",
        ),
    )

    assert response.status_code == 202
    installation.refresh_from_db()

    assert installation.account_login == "renamed-org"
    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert installation.lifecycle_generation == 1
    assert AnalysisJob.objects.filter(job_type="reconcile_installation").count() == 1
