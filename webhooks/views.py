import hashlib
import hmac
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from github_integration.models import GitHubInstallation
from jobs.models import AnalysisJob
from webhooks.models import WebhookDelivery

SUPPORTED_PULL_REQUEST_ACTIONS = {
    "opened",
    "synchronize",
    "reopened",
    "ready_for_review",
}

SUPPORTED_INSTALLATION_ACTIONS = {
    "created",
    "suspend",
    "unsuspend",
    "deleted",
}


def _valid_signature(secret: str, body: bytes, supplied: str) -> bool:
    if not supplied.startswith("sha256="):
        return False

    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    return constant_time_compare(expected, supplied)


def _positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _installation_account(installation_data):
    """Return validated account metadata or None."""
    account = installation_data.get("account")
    if not isinstance(account, dict):
        return None

    login = account.get("login")
    account_type = account.get("type")

    if not isinstance(login, str) or not login.strip():
        return None
    if not isinstance(account_type, str):
        return None

    account_type = account_type.strip().lower()
    valid_types = {choice for choice, _ in GitHubInstallation.AccountType.choices}
    if account_type not in valid_types:
        return None

    return {
        "account_login": login.strip(),
        "account_type": account_type,
    }


def _schedule_installation_reconciliation(
    installation_id: int,
    account_data: dict,
) -> int:
    """
    Increment the lifecycle generation while holding the installation row lock.

    The caller must be inside transaction.atomic().
    """
    installation, _ = GitHubInstallation.objects.get_or_create(
        installation_id=installation_id,
        defaults={
            **account_data,
            "status": GitHubInstallation.Status.ACTIVE,
        },
    )

    installation = GitHubInstallation.objects.select_for_update().get(
        pk=installation.pk,
    )

    GitHubInstallation.objects.filter(pk=installation.pk).update(
        account_login=account_data["account_login"],
        account_type=account_data["account_type"],
        lifecycle_generation=F("lifecycle_generation") + 1,
        last_lifecycle_event_at=timezone.now(),
    )

    installation.refresh_from_db(
        fields=["lifecycle_generation"],
    )
    generation = installation.lifecycle_generation

    AnalysisJob.objects.get_or_create(
        dedupe_key=f"installation-reconcile:{installation_id}:{generation}",
        defaults={
            "job_type": "reconcile_installation",
            "payload": {
                "installation_id": installation_id,
                "lifecycle_generation": generation,
            },
        },
    )

    return generation


@csrf_exempt
@require_POST
def github_webhook(request):
    secret = settings.GITHUB_WEBHOOK_SECRET
    if not secret:
        return JsonResponse(
            {"error": "webhook_not_configured"},
            status=503,
        )

    body = request.body
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _valid_signature(secret, body, signature):
        return JsonResponse({"error": "invalid_signature"}, status=401)

    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    event_type = request.headers.get("X-GitHub-Event", "").strip()

    if not delivery_id or len(delivery_id) > 128 or not event_type:
        return JsonResponse(
            {"error": "missing_or_invalid_webhook_headers"},
            status=400,
        )

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_payload"}, status=400)

    action = payload.get("action", "")
    if not isinstance(action, str):
        return JsonResponse({"error": "invalid_action"}, status=400)

    installation_data = payload.get("installation")
    installation_id = None
    account_data = None

    if installation_data is not None:
        if not isinstance(installation_data, dict):
            return JsonResponse(
                {"error": "invalid_installation"},
                status=400,
            )

        installation_id = _positive_int(installation_data.get("id"))
        if installation_id is None:
            return JsonResponse(
                {"error": "invalid_installation_id"},
                status=400,
            )

    repository_data = payload.get("repository")
    repository_id = None

    if repository_data is not None:
        if not isinstance(repository_data, dict):
            return JsonResponse(
                {"error": "invalid_repository"},
                status=400,
            )

        repository_id = _positive_int(repository_data.get("id"))
        if repository_id is None:
            return JsonResponse(
                {"error": "invalid_repository_id"},
                status=400,
            )

    payload_hash = hashlib.sha256(body).hexdigest()
    delivery_status = WebhookDelivery.Status.IGNORED
    job_data = None
    is_installation_lifecycle = (
        event_type == "installation" and action in SUPPORTED_INSTALLATION_ACTIONS
    )

    if event_type == "pull_request" and action in SUPPORTED_PULL_REQUEST_ACTIONS:
        pull_request = payload.get("pull_request")
        if not isinstance(pull_request, dict):
            return JsonResponse(
                {"error": "missing_pull_request"},
                status=400,
            )

        number = _positive_int(payload.get("number"))
        base = pull_request.get("base")
        head = pull_request.get("head")

        if number is None or not isinstance(base, dict) or not isinstance(head, dict):
            return JsonResponse(
                {"error": "invalid_pull_request_structure"},
                status=400,
            )

        base_sha = base.get("sha")
        head_sha = head.get("sha")
        if (
            not isinstance(base_sha, str)
            or not base_sha
            or not isinstance(head_sha, str)
            or not head_sha
        ):
            return JsonResponse(
                {"error": "missing_commit_sha"},
                status=400,
            )

        if installation_id is None or repository_id is None:
            return JsonResponse(
                {"error": "missing_installation_or_repository"},
                status=400,
            )

        delivery_status = WebhookDelivery.Status.ACCEPTED
        job_data = {
            "job_type": "analyze_pull_request",
            "dedupe_key": f"pr-analysis:{repository_id}:{number}:{head_sha}",
            "payload": {
                "installation_id": installation_id,
                "repository_id": repository_id,
                "pull_request_number": number,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "action": action,
            },
        }

    elif is_installation_lifecycle:
        if installation_id is None:
            return JsonResponse(
                {"error": "missing_installation_id"},
                status=400,
            )

        account_data = _installation_account(installation_data)
        if account_data is None:
            return JsonResponse(
                {"error": "invalid_installation_account"},
                status=400,
            )

        delivery_status = WebhookDelivery.Status.ACCEPTED

    try:
        with transaction.atomic():
            delivery = WebhookDelivery.objects.create(
                delivery_id=delivery_id,
                event_type=event_type,
                action=action,
                status=delivery_status,
                payload_sha256=payload_hash,
                installation_id=installation_id,
                repository_id=repository_id,
            )

            if is_installation_lifecycle:
                _schedule_installation_reconciliation(
                    installation_id=installation_id,
                    account_data=account_data,
                )

            elif job_data is not None:
                AnalysisJob.objects.get_or_create(
                    dedupe_key=job_data["dedupe_key"],
                    defaults={
                        "job_type": job_data["job_type"],
                        "payload": job_data["payload"],
                    },
                )

    except IntegrityError:
        if WebhookDelivery.objects.filter(delivery_id=delivery_id).exists():
            return JsonResponse(
                {"status": "duplicate", "delivery_id": delivery_id},
                status=200,
            )

        return JsonResponse(
            {"error": "persistence_conflict"},
            status=503,
        )

    return JsonResponse(
        {
            "status": "accepted",
            "delivery_id": delivery.delivery_id,
            "job_created_or_existing": (
                is_installation_lifecycle or job_data is not None
            ),
        },
        status=202,
    )
