from unittest.mock import Mock

import pytest
from django.db.models import F

from github_integration.client import GitHubResponseError
from github_integration.models import GitHubInstallation, Repository
from github_integration.services import (
    reconcile_installation,
    sync_installation_repositories,
)


@pytest.fixture
def installation(db):
    return GitHubInstallation.objects.create(
        installation_id=9876,
        account_login="flowpilot",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
    )


@pytest.fixture
def repository(installation):
    return Repository.objects.create(
        installation=installation,
        github_repo_id=4567,
        full_name="flowpilot/example",
        default_branch="main",
        html_url="https://github.com/flowpilot/example",
        is_private=True,
    )


def active_remote_installation(installation_id=9876):
    return {
        "id": installation_id,
        "account": {
            "login": "flowpilot",
            "type": "Organization",
        },
        "suspended_at": None,
    }


def suspended_remote_installation(installation_id=9876):
    return {
        "id": installation_id,
        "account": {
            "login": "flowpilot",
            "type": "Organization",
        },
        "suspended_at": "2026-10-20T07:00:00Z",
    }


def remote_repository(repository_id=4567):
    return {
        "id": repository_id,
        "full_name": "flowpilot/example",
        "html_url": "https://github.com/flowpilot/example",
        "default_branch": "main",
        "private": True,
    }


@pytest.mark.django_db
def test_active_installation_reconciles_and_syncs_repositories(installation):
    client = Mock()
    client.get_installation.return_value = active_remote_installation()
    client.list_installation_repositories.return_value = [
        remote_repository(),
    ]

    result = reconcile_installation(
        installation.installation_id,
        installation.lifecycle_generation,
        client=client,
    )

    installation.refresh_from_db()
    repository = Repository.objects.get(github_repo_id=4567)

    assert result.stale is False
    assert result.status == GitHubInstallation.Status.ACTIVE
    assert result.repository_sync is not None
    assert result.repository_sync.total == 1
    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert repository.status == Repository.Status.ACTIVE
    client.get_installation.assert_called_once_with(9876)
    client.list_installation_repositories.assert_called_once_with(9876)


@pytest.mark.django_db
def test_stale_generation_is_rejected_before_github_request(installation):
    client = Mock()

    GitHubInstallation.objects.filter(pk=installation.pk).update(
        lifecycle_generation=F("lifecycle_generation") + 1,
    )

    result = reconcile_installation(
        installation.installation_id,
        lifecycle_generation=0,
        client=client,
    )

    assert result.stale is True
    assert result.repository_sync is None
    client.get_installation.assert_not_called()
    client.list_installation_repositories.assert_not_called()


@pytest.mark.django_db
def test_generation_change_during_installation_request_discards_response(
    installation,
):
    def get_installation(installation_id):
        GitHubInstallation.objects.filter(
            pk=installation.pk,
        ).update(
            lifecycle_generation=F("lifecycle_generation") + 1,
        )
        return suspended_remote_installation(installation_id)

    client = Mock()
    client.get_installation.side_effect = get_installation

    result = reconcile_installation(
        installation.installation_id,
        lifecycle_generation=0,
        client=client,
    )

    installation.refresh_from_db()

    assert result.stale is True
    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert installation.lifecycle_generation == 1
    client.list_installation_repositories.assert_not_called()


@pytest.mark.django_db
def test_generation_change_during_repository_listing_discards_inventory(
    installation,
    repository,
):
    def list_repositories(installation_id):
        GitHubInstallation.objects.filter(
            pk=installation.pk,
        ).update(
            lifecycle_generation=F("lifecycle_generation") + 1,
        )
        return []

    client = Mock()
    client.get_installation.return_value = active_remote_installation()
    client.list_installation_repositories.side_effect = list_repositories

    result = reconcile_installation(
        installation.installation_id,
        lifecycle_generation=0,
        client=client,
    )

    repository.refresh_from_db()
    installation.refresh_from_db()

    assert result.stale is True
    assert result.repository_sync is not None
    assert result.repository_sync.stale is True
    assert repository.status == Repository.Status.ACTIVE
    assert installation.lifecycle_generation == 1


@pytest.mark.django_db
def test_suspended_remote_installation_disables_repositories(
    installation,
    repository,
):
    client = Mock()
    client.get_installation.return_value = suspended_remote_installation()

    result = reconcile_installation(
        installation.installation_id,
        lifecycle_generation=0,
        client=client,
    )

    installation.refresh_from_db()
    repository.refresh_from_db()

    assert result.stale is False
    assert result.status == GitHubInstallation.Status.SUSPENDED
    assert result.repository_sync is None
    assert installation.status == GitHubInstallation.Status.SUSPENDED
    assert repository.status == Repository.Status.DISABLED
    client.list_installation_repositories.assert_not_called()


@pytest.mark.django_db
def test_github_api_failure_does_not_change_installation_state(installation):
    client = Mock()
    client.get_installation.side_effect = RuntimeError(
        "temporary GitHub API failure",
    )

    with pytest.raises(RuntimeError, match="temporary GitHub API failure"):
        reconcile_installation(
            installation.installation_id,
            lifecycle_generation=0,
            client=client,
        )

    installation.refresh_from_db()
    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert installation.lifecycle_generation == 0


@pytest.mark.django_db
def test_invalid_remote_installation_response_does_not_change_state(
    installation,
):
    client = Mock()
    client.get_installation.return_value = {
        "id": installation.installation_id + 1,
        "account": {
            "login": "other",
            "type": "Organization",
        },
        "suspended_at": None,
    }

    with pytest.raises(GitHubResponseError):
        reconcile_installation(
            installation.installation_id,
            lifecycle_generation=0,
            client=client,
        )

    installation.refresh_from_db()
    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert installation.account_login == "flowpilot"
    assert installation.lifecycle_generation == 0


@pytest.mark.django_db
def test_removed_installation_disables_repositories(
    installation,
    repository,
):
    from github_integration.client import GitHubNotFoundError

    client = Mock()
    client.get_installation.side_effect = GitHubNotFoundError(
        "installation not found",
    )

    result = reconcile_installation(
        installation.installation_id,
        lifecycle_generation=0,
        client=client,
    )

    installation.refresh_from_db()
    repository.refresh_from_db()

    assert result.stale is False
    assert result.status == GitHubInstallation.Status.REMOVED
    assert result.repository_sync is None
    assert installation.status == GitHubInstallation.Status.REMOVED
    assert repository.status == Repository.Status.DISABLED
    client.list_installation_repositories.assert_not_called()


@pytest.mark.django_db
def test_repository_sync_rejects_stale_generation_before_listing(
    installation,
):
    GitHubInstallation.objects.filter(pk=installation.pk).update(
        lifecycle_generation=F("lifecycle_generation") + 1,
    )
    client = Mock()

    result = sync_installation_repositories(
        installation,
        client=client,
        expected_generation=0,
    )

    assert result.stale is True
    client.list_installation_repositories.assert_not_called()
