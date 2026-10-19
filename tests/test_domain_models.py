import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from dashboard.models import DashboardMembership
from github_integration.models import GitHubInstallation, Repository


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


def test_installation_defaults_to_active(installation):
    assert installation.status == GitHubInstallation.Status.ACTIVE
    assert installation.installation_id == 12345


def test_repository_belongs_to_installation(installation, repository):
    assert repository.installation == installation
    assert repository.full_name == "flowpilot-test/example"
    assert repository.status == Repository.Status.ACTIVE


@pytest.mark.django_db
def test_installation_id_must_be_unique(installation):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            GitHubInstallation.objects.create(
                installation_id=installation.installation_id,
                account_login="duplicate",
                account_type=GitHubInstallation.AccountType.USER,
            )


def test_membership_grants_repository_access(repository):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="flowpilot-test-user",
        password="test-password-only",
    )

    membership = DashboardMembership.objects.create(
        user=user,
        repository=repository,
        role=DashboardMembership.Role.VIEWER,
    )

    assert membership.role == DashboardMembership.Role.VIEWER
    assert membership.repository == repository
    assert membership.user == user


def test_user_cannot_have_duplicate_membership(repository):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="flowpilot-duplicate-test",
        password="test-password-only",
    )

    DashboardMembership.objects.create(
        user=user,
        repository=repository,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            DashboardMembership.objects.create(
                user=user,
                repository=repository,
            )
