import uuid
from unittest.mock import patch

import pytest

from github_integration.models import GitHubInstallation
from jobs.management.commands.run_worker import (
    reconcile_installation_job,
    sync_installation_job,
)
from jobs.worker import ClaimedJob


@pytest.fixture
def installation(db):
    return GitHubInstallation.objects.create(
        installation_id=9876,
        account_login="flowpilot",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
    )


def make_claim(
    installation_id=9876,
    *,
    job_type="sync_installation_repositories",
    lifecycle_generation=None,
):
    payload = {"installation_id": installation_id}
    if lifecycle_generation is not None:
        payload["lifecycle_generation"] = lifecycle_generation

    return ClaimedJob(
        job_id=uuid.uuid4(),
        job_type=job_type,
        payload=payload,
        attempt=1,
        lease_token=uuid.uuid4(),
    )


@pytest.mark.django_db
@patch("jobs.management.commands.run_worker.sync_installation_repositories")
def test_active_installation_is_synchronized(mock_sync, installation):
    sync_installation_job(make_claim())

    mock_sync.assert_called_once_with(installation)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        GitHubInstallation.Status.SUSPENDED,
        GitHubInstallation.Status.REMOVED,
    ],
)
@patch("jobs.management.commands.run_worker.sync_installation_repositories")
def test_inactive_installation_is_not_synchronized(
    mock_sync,
    installation,
    status,
):
    installation.status = status
    installation.save(update_fields=["status", "updated_at"])

    sync_installation_job(make_claim())

    mock_sync.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("installation_id", [0, -1, True, "9876", None])
def test_invalid_installation_id_is_rejected(installation_id):
    with pytest.raises(ValueError, match="Invalid GitHub installation ID"):
        sync_installation_job(make_claim(installation_id))


@pytest.mark.django_db
def test_unknown_installation_raises_for_worker_retry():
    with pytest.raises(GitHubInstallation.DoesNotExist):
        sync_installation_job(make_claim(installation_id=9876))


@pytest.mark.django_db
@patch(
    "jobs.management.commands.run_worker.sync_installation_repositories",
    side_effect=RuntimeError("temporary GitHub failure"),
)
def test_sync_failure_propagates_for_worker_retry(mock_sync, installation):
    with pytest.raises(RuntimeError, match="temporary GitHub failure"):
        sync_installation_job(make_claim())

    mock_sync.assert_called_once_with(installation)


@pytest.mark.django_db
@patch("jobs.management.commands.run_worker.reconcile_installation")
def test_reconciliation_job_dispatches_installation_and_generation(
    mock_reconcile,
    installation,
):
    claim = make_claim(
        installation.installation_id,
        job_type="reconcile_installation",
        lifecycle_generation=4,
    )

    reconcile_installation_job(claim)

    mock_reconcile.assert_called_once()
    args, kwargs = mock_reconcile.call_args

    assert args == ()
    assert kwargs["installation_id"] == installation.installation_id
    assert kwargs["lifecycle_generation"] == 4


@pytest.mark.django_db
@pytest.mark.parametrize(
    "installation_id",
    [0, -1, True, "9876", None],
)
@patch("jobs.management.commands.run_worker.reconcile_installation")
def test_reconciliation_job_rejects_invalid_installation_id(
    mock_reconcile,
    installation_id,
):
    claim = make_claim(
        installation_id,
        job_type="reconcile_installation",
        lifecycle_generation=0,
    )

    with pytest.raises(ValueError, match="Invalid GitHub installation ID"):
        reconcile_installation_job(claim)

    mock_reconcile.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "generation",
    [-1, True, "1", None],
)
@patch("jobs.management.commands.run_worker.reconcile_installation")
def test_reconciliation_job_rejects_invalid_generation(
    mock_reconcile,
    installation,
    generation,
):
    claim = make_claim(
        installation.installation_id,
        job_type="reconcile_installation",
        lifecycle_generation=generation,
    )

    with pytest.raises(ValueError, match="Invalid lifecycle generation"):
        reconcile_installation_job(claim)

    mock_reconcile.assert_not_called()


@pytest.mark.django_db
@patch(
    "jobs.management.commands.run_worker.reconcile_installation",
    side_effect=RuntimeError("temporary GitHub failure"),
)
def test_reconciliation_failure_propagates_for_worker_retry(
    mock_reconcile,
    installation,
):
    claim = make_claim(
        installation.installation_id,
        job_type="reconcile_installation",
        lifecycle_generation=0,
    )

    with pytest.raises(RuntimeError, match="temporary GitHub failure"):
        reconcile_installation_job(claim)

    mock_reconcile.assert_called_once()
