from unittest.mock import Mock

import pytest

from github_integration.client import GitHubResponseError
from github_integration.models import GitHubInstallation, Repository
from github_integration.services import sync_installation_repositories


@pytest.fixture
def installation(db):
    return GitHubInstallation.objects.create(
        installation_id=9876,
        account_login="flowpilot",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
    )


def make_repository(repository_id, **overrides):
    repository = {
        "id": repository_id,
        "full_name": f"flowpilot/repo-{repository_id}",
        "html_url": f"https://github.com/flowpilot/repo-{repository_id}",
        "default_branch": "main",
        "private": True,
    }
    repository.update(overrides)
    return repository


def make_client(repositories):
    client = Mock()
    client.list_installation_repositories.return_value = repositories
    return client


@pytest.mark.django_db
def test_sync_creates_new_repositories(installation):
    client = make_client(
        [
            make_repository(101),
            make_repository(102, private=False),
        ]
    )

    result = sync_installation_repositories(
        installation,
        client=client,
    )

    assert result.total == 2
    assert result.created == 2
    assert result.updated == 0
    assert result.disabled == 0

    repository = Repository.objects.get(github_repo_id=102)
    assert repository.installation == installation
    assert repository.status == Repository.Status.ACTIVE
    assert repository.is_private is False


@pytest.mark.django_db
def test_sync_updates_existing_repositories(installation):
    existing = Repository.objects.create(
        installation=installation,
        github_repo_id=101,
        full_name="old-name/repo",
        default_branch="master",
        html_url="https://github.com/old-name/repo",
        is_private=True,
    )

    client = make_client(
        [
            make_repository(
                101,
                full_name="flowpilot/renamed-repo",
                html_url="https://github.com/flowpilot/renamed-repo",
                default_branch="main",
                private=False,
            )
        ]
    )

    result = sync_installation_repositories(
        installation,
        client=client,
    )

    existing.refresh_from_db()

    assert result.created == 0
    assert result.updated == 1
    assert result.total == 1
    assert existing.full_name == "flowpilot/renamed-repo"
    assert existing.default_branch == "main"
    assert existing.is_private is False
    assert existing.status == Repository.Status.ACTIVE
    assert Repository.objects.filter(github_repo_id=101).count() == 1


@pytest.mark.django_db
def test_sync_disables_repositories_missing_from_github(installation):
    stale = Repository.objects.create(
        installation=installation,
        github_repo_id=101,
        full_name="flowpilot/old-repo",
        default_branch="main",
        html_url="https://github.com/flowpilot/old-repo",
    )

    client = make_client([make_repository(102)])

    result = sync_installation_repositories(
        installation,
        client=client,
    )

    stale.refresh_from_db()

    assert result.created == 1
    assert result.disabled == 1
    assert stale.status == Repository.Status.DISABLED


@pytest.mark.django_db
def test_sync_reactivates_a_previously_disabled_repository(installation):
    existing = Repository.objects.create(
        installation=installation,
        github_repo_id=101,
        full_name="flowpilot/repo-101",
        default_branch="main",
        html_url="https://github.com/flowpilot/repo-101",
        status=Repository.Status.DISABLED,
    )

    result = sync_installation_repositories(
        installation,
        client=make_client([make_repository(101)]),
    )

    existing.refresh_from_db()

    assert result.updated == 1
    assert existing.status == Repository.Status.ACTIVE


@pytest.mark.django_db
def test_api_failure_does_not_change_existing_repositories(installation):
    existing = Repository.objects.create(
        installation=installation,
        github_repo_id=101,
        full_name="flowpilot/repo-101",
        default_branch="main",
        html_url="https://github.com/flowpilot/repo-101",
    )

    client = Mock()
    client.list_installation_repositories.side_effect = GitHubResponseError(
        "GitHub returned an invalid repository list."
    )

    with pytest.raises(GitHubResponseError):
        sync_installation_repositories(installation, client=client)

    existing.refresh_from_db()
    assert existing.status == Repository.Status.ACTIVE
    assert Repository.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "repositories",
    [
        [make_repository(101), make_repository(101)],
        [make_repository(0)],
        [make_repository(101, full_name="")],
        [make_repository(101, html_url="http://github.com/repo")],
        [make_repository(101, default_branch=123)],
        [make_repository(101, private="yes")],
    ],
)
def test_invalid_response_does_not_change_existing_repositories(
    installation,
    repositories,
):
    existing = Repository.objects.create(
        installation=installation,
        github_repo_id=200,
        full_name="flowpilot/existing",
        default_branch="main",
        html_url="https://github.com/flowpilot/existing",
    )

    with pytest.raises(GitHubResponseError):
        sync_installation_repositories(
            installation,
            client=make_client(repositories),
        )

    existing.refresh_from_db()
    assert existing.status == Repository.Status.ACTIVE
    assert Repository.objects.count() == 1


@pytest.mark.django_db
def test_inactive_installation_cannot_be_synchronized(installation):
    installation.status = GitHubInstallation.Status.SUSPENDED
    installation.save(update_fields=["status", "updated_at"])

    client = Mock()

    with pytest.raises(ValueError, match="Only active"):
        sync_installation_repositories(
            installation,
            client=client,
        )

    client.list_installation_repositories.assert_not_called()
