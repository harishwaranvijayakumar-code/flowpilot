import hashlib
import hmac
import json

import pytest
from django.test import Client
from django.test.utils import override_settings

from github_integration.models import GitHubInstallation
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
def test_duplicate_delivery_does_not_duplicate_job(client, installation):
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
    delivery = WebhookDelivery.objects.get()
    assert delivery.status == WebhookDelivery.Status.IGNORED
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
def test_installation_suspension_updates_existing_installation(
    client,
    installation,
):
    payload = {
        "action": "suspend",
        "installation": {"id": installation.installation_id},
    }
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/github/",
        data=body,
        **signed_headers(
            body,
            delivery_id="delivery-suspend",
            event="installation",
        ),
    )

    assert response.status_code == 202
    installation.refresh_from_db()
    assert installation.status == GitHubInstallation.Status.SUSPENDED
    assert AnalysisJob.objects.count() == 0
