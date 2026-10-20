from dataclasses import dataclass
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from github_integration.client import (
    GitHubAppClient,
    GitHubNotFoundError,
    GitHubResponseError,
)
from github_integration.models import (
    GitHubInstallation,
    Repository,
)


@dataclass(frozen=True)
class RepositorySyncResult:
    """Summary of a repository synchronization attempt."""

    total: int
    created: int
    updated: int
    disabled: int
    stale: bool = False


@dataclass(frozen=True)
class InstallationReconciliationResult:
    """Summary of an authoritative installation reconciliation."""

    installation_id: int
    status: str | None
    stale: bool
    repository_sync: RepositorySyncResult | None = None


def _validate_repositories(repositories: list[dict]) -> list[dict]:
    """Validate the complete GitHub response before modifying the database."""
    validated = []
    seen_ids = set()

    for repository in repositories:
        if not isinstance(repository, dict):
            raise GitHubResponseError("GitHub returned an invalid repository entry.")

        repository_id = repository.get("id")
        full_name = repository.get("full_name")
        html_url = repository.get("html_url")
        default_branch = repository.get("default_branch", "")
        is_private = repository.get("private")

        if (
            isinstance(repository_id, bool)
            or not isinstance(repository_id, int)
            or repository_id <= 0
        ):
            raise GitHubResponseError("GitHub returned an invalid repository ID.")

        if repository_id in seen_ids:
            raise GitHubResponseError("GitHub returned a duplicate repository ID.")
        seen_ids.add(repository_id)

        if (
            not isinstance(full_name, str)
            or not full_name.strip()
            or len(full_name.strip()) > 255
        ):
            raise GitHubResponseError("GitHub returned an invalid repository name.")

        if not isinstance(html_url, str) or len(html_url) > 500:
            raise GitHubResponseError("GitHub returned an invalid repository URL.")

        parsed_url = urlsplit(html_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise GitHubResponseError("GitHub returned an invalid repository URL.")

        if default_branch is None:
            default_branch = ""

        if not isinstance(default_branch, str) or len(default_branch) > 255:
            raise GitHubResponseError("GitHub returned an invalid default branch.")

        if not isinstance(is_private, bool):
            raise GitHubResponseError(
                "GitHub returned an invalid repository visibility value."
            )

        validated.append(
            {
                "github_repo_id": repository_id,
                "full_name": full_name.strip(),
                "html_url": html_url,
                "default_branch": default_branch,
                "is_private": is_private,
            }
        )

    return validated


def _inactive_sync_result() -> RepositorySyncResult:
    """Represent a sync safely skipped because its generation is stale."""
    return RepositorySyncResult(
        total=0,
        created=0,
        updated=0,
        disabled=0,
        stale=True,
    )


def sync_installation_repositories(
    installation: GitHubInstallation,
    *,
    client: GitHubAppClient | None = None,
    expected_generation: int | None = None,
) -> RepositorySyncResult:
    """
    Synchronize repositories accessible to an active installation.

    When expected_generation is provided, the sync is tied to that lifecycle
    generation. The generation and status are checked both before the GitHub
    request and under a row lock before applying database changes.
    """
    if not isinstance(installation, GitHubInstallation):
        raise TypeError("installation must be a GitHubInstallation instance.")

    if expected_generation is not None and (
        isinstance(expected_generation, bool)
        or not isinstance(expected_generation, int)
        or expected_generation < 0
    ):
        raise ValueError("Expected lifecycle generation must be non-negative.")

    installation_pk = installation.pk

    # Reject an already stale job before making a GitHub API request.
    with transaction.atomic():
        current = GitHubInstallation.objects.select_for_update().get(
            pk=installation_pk,
        )

        if expected_generation is not None:
            if (
                current.lifecycle_generation != expected_generation
                or current.status != GitHubInstallation.Status.ACTIVE
            ):
                return _inactive_sync_result()
        elif current.status != GitHubInstallation.Status.ACTIVE:
            raise ValueError("Only active GitHub installations can be synchronized.")

        installation_id = current.installation_id

    owns_client = client is None
    github_client = client or GitHubAppClient.from_settings()

    try:
        github_repositories = github_client.list_installation_repositories(
            installation_id
        )
    finally:
        if owns_client:
            github_client.close()

    if not isinstance(github_repositories, list):
        raise GitHubResponseError("GitHub returned an invalid repository list.")

    validated = _validate_repositories(github_repositories)
    repository_ids = [repository["github_repo_id"] for repository in validated]

    created = 0
    updated = 0

    with transaction.atomic():
        # Recheck after the network request. A lifecycle event may have
        # arrived while GitHub was returning the repository inventory.
        current = GitHubInstallation.objects.select_for_update().get(
            pk=installation_pk,
        )

        if expected_generation is not None and (
            current.lifecycle_generation != expected_generation
            or current.status != GitHubInstallation.Status.ACTIVE
        ):
            return _inactive_sync_result()

        if current.status != GitHubInstallation.Status.ACTIVE:
            raise ValueError(
                "Installation became inactive during repository synchronization."
            )

        for repository_data in validated:
            _, was_created = Repository.objects.update_or_create(
                github_repo_id=repository_data["github_repo_id"],
                defaults={
                    **repository_data,
                    "installation": current,
                    "status": Repository.Status.ACTIVE,
                },
            )

            if was_created:
                created += 1
            else:
                updated += 1

        stale_repositories = Repository.objects.filter(
            installation=current,
        )

        if repository_ids:
            stale_repositories = stale_repositories.exclude(
                github_repo_id__in=repository_ids,
            )

        disabled = stale_repositories.exclude(
            status=Repository.Status.DISABLED,
        ).update(
            status=Repository.Status.DISABLED,
            updated_at=timezone.now(),
        )

    return RepositorySyncResult(
        total=len(validated),
        created=created,
        updated=updated,
        disabled=disabled,
    )


def _validate_installation_response(
    data: dict,
    expected_installation_id: int,
) -> tuple[dict, str]:
    """Validate the fields needed to determine installation state."""
    if not isinstance(data, dict):
        raise GitHubResponseError("GitHub returned an invalid installation response.")

    returned_id = data.get("id")
    if (
        isinstance(returned_id, bool)
        or not isinstance(returned_id, int)
        or returned_id != expected_installation_id
    ):
        raise GitHubResponseError("GitHub returned an unexpected installation ID.")

    account = data.get("account")
    if not isinstance(account, dict):
        raise GitHubResponseError("GitHub returned invalid installation account data.")

    login = account.get("login")
    account_type = account.get("type")

    if (
        not isinstance(login, str)
        or not login.strip()
        or len(login.strip()) > 255
        or not isinstance(account_type, str)
    ):
        raise GitHubResponseError("GitHub returned invalid installation account data.")

    normalized_type = account_type.strip().lower()
    valid_types = {choice for choice, _ in GitHubInstallation.AccountType.choices}
    if normalized_type not in valid_types:
        raise GitHubResponseError(
            "GitHub returned an unsupported installation account type."
        )

    if "suspended_at" not in data:
        raise GitHubResponseError(
            "GitHub installation response is missing suspended_at."
        )

    suspended_at = data["suspended_at"]
    if suspended_at is not None and (
        not isinstance(suspended_at, str) or not suspended_at.strip()
    ):
        raise GitHubResponseError("GitHub returned an invalid suspension timestamp.")

    status = (
        GitHubInstallation.Status.SUSPENDED
        if suspended_at is not None
        else GitHubInstallation.Status.ACTIVE
    )

    return (
        {
            "account_login": login.strip(),
            "account_type": normalized_type,
        },
        status,
    )


def reconcile_installation(
    installation_id: int,
    lifecycle_generation: int,
    *,
    client: GitHubAppClient | None = None,
) -> InstallationReconciliationResult:
    """
    Reconcile local installation state against GitHub's current state.

    Results from obsolete lifecycle generations are discarded. GitHub API
    failures other than a not-found response propagate to the worker so its
    existing retry mechanism can handle them.
    """
    if (
        isinstance(installation_id, bool)
        or not isinstance(installation_id, int)
        or installation_id <= 0
    ):
        raise ValueError("Installation ID must be a positive integer.")

    if (
        isinstance(lifecycle_generation, bool)
        or not isinstance(lifecycle_generation, int)
        or lifecycle_generation < 0
    ):
        raise ValueError("Lifecycle generation must be non-negative.")

    with transaction.atomic():
        installation = GitHubInstallation.objects.select_for_update().get(
            installation_id=installation_id,
        )

        if installation.lifecycle_generation != lifecycle_generation:
            return InstallationReconciliationResult(
                installation_id=installation_id,
                status=installation.status,
                stale=True,
            )

    owns_client = client is None
    github_client = client or GitHubAppClient.from_settings()

    try:
        try:
            remote_data = github_client.get_installation(installation_id)
        except GitHubNotFoundError:
            # A 404 from the App-authenticated installation endpoint means
            # this App cannot find the installation. Treat it as removed.
            remote_data = None

        if remote_data is None:
            account_data = None
            remote_status = GitHubInstallation.Status.REMOVED
        else:
            account_data, remote_status = _validate_installation_response(
                remote_data,
                installation_id,
            )

        with transaction.atomic():
            installation = GitHubInstallation.objects.select_for_update().get(
                installation_id=installation_id,
            )

            if installation.lifecycle_generation != lifecycle_generation:
                return InstallationReconciliationResult(
                    installation_id=installation_id,
                    status=installation.status,
                    stale=True,
                )

            installation.status = remote_status
            if account_data is not None:
                installation.account_login = account_data["account_login"]
                installation.account_type = account_data["account_type"]

            installation.save(
                update_fields=[
                    "status",
                    "account_login",
                    "account_type",
                    "updated_at",
                ]
            )

            if remote_status != GitHubInstallation.Status.ACTIVE:
                Repository.objects.filter(
                    installation=installation,
                ).exclude(
                    status=Repository.Status.DISABLED,
                ).update(
                    status=Repository.Status.DISABLED,
                    updated_at=timezone.now(),
                )

        if remote_status != GitHubInstallation.Status.ACTIVE:
            return InstallationReconciliationResult(
                installation_id=installation_id,
                status=remote_status,
                stale=False,
            )

        sync_result = sync_installation_repositories(
            installation,
            client=github_client,
            expected_generation=lifecycle_generation,
        )

        return InstallationReconciliationResult(
            installation_id=installation_id,
            status=remote_status,
            stale=sync_result.stale,
            repository_sync=sync_result,
        )
    finally:
        if owns_client:
            github_client.close()
