import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from django.test import Client

from github_integration.models import GitHubInstallation
from jobs.models import AnalysisJob
from webhooks.models import WebhookDelivery

SECRET = "test-webhook-secret"


def _signed_headers(body, delivery_id):
    signature = hmac.new(
        SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return {
        "HTTP_X_HUB_SIGNATURE_256": f"sha256={signature}",
        "HTTP_X_GITHUB_DELIVERY": delivery_id,
        "HTTP_X_GITHUB_EVENT": "installation",
        "content_type": "application/json",
    }


def _send_delivery(barrier, action, delivery_id):
    close_old_connections()
    try:
        payload = {
            "action": action,
            "installation": {
                "id": 9876,
                "account": {
                    "login": "flowpilot",
                    "type": "Organization",
                },
            },
        }
        body = json.dumps(payload).encode()

        barrier.wait(timeout=10)
        response = Client().post(
            "/webhooks/github/",
            data=body,
            **_signed_headers(body, delivery_id),
        )
        return response.status_code, response.json()
    finally:
        # Close every database connection owned by this worker thread.
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="This concurrency test requires PostgreSQL row-lock semantics.",
)
def test_concurrent_lifecycle_deliveries_get_distinct_generations(
    settings,
):
    settings.GITHUB_WEBHOOK_SECRET = SECRET

    installation = GitHubInstallation.objects.create(
        installation_id=9876,
        account_login="flowpilot",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
    )

    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            _send_delivery,
            barrier,
            "created",
            "concurrent-delivery-created",
        )
        second = executor.submit(
            _send_delivery,
            barrier,
            "unsuspend",
            "concurrent-delivery-unsuspend",
        )

        results = [first.result(timeout=30), second.result(timeout=30)]

    assert [status for status, _ in results] == [202, 202]
    assert all(body["status"] == "accepted" for _, body in results)

    installation.refresh_from_db()
    assert installation.lifecycle_generation == 2

    assert (
        WebhookDelivery.objects.filter(
            delivery_id__in=[
                "concurrent-delivery-created",
                "concurrent-delivery-unsuspend",
            ],
        ).count()
        == 2
    )

    jobs = AnalysisJob.objects.filter(
        job_type="reconcile_installation",
        payload__installation_id=9876,
    )
    assert jobs.count() == 2
    assert {job.payload["lifecycle_generation"] for job in jobs} == {1, 2}
    assert {job.dedupe_key for job in jobs} == {
        "installation-reconcile:9876:1",
        "installation-reconcile:9876:2",
    }
