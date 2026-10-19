import base64
import binascii
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from github_integration.client import (
    GitHubAppClient,
    GitHubNotFoundError,
)
from github_integration.models import GitHubInstallation, Repository

WORKFLOW_DIRECTORY = ".github/workflows/"
WORKFLOW_EXTENSIONS = (".yml", ".yaml")
FILES_PER_PAGE = 100
MAX_PULL_REQUEST_FILES = 3000


class WorkflowRevisionError(Exception):
    """Base exception for pull-request workflow revision retrieval."""


class WorkflowRevisionNotFoundError(WorkflowRevisionError):
    """A repository, pull request, or requested revision is unavailable."""


class WorkflowRevisionPayloadError(WorkflowRevisionError):
    """GitHub returned malformed or incomplete revision data."""


@dataclass(frozen=True)
class WorkflowRevisionPair:
    """Workflow contents at the base and head revisions."""

    path: str
    before_yaml: str
    after_yaml: str
    status: str
    previous_path: str | None = None
    current_path: str | None = None


def is_workflow_path(path: str) -> bool:
    """Return whether a path is a supported GitHub Actions workflow file."""
    normalized = path.replace("\\", "/")
    return (
        normalized.startswith(WORKFLOW_DIRECTORY)
        and normalized.lower().endswith(WORKFLOW_EXTENSIONS)
        and not normalized.endswith("/")
    )


def _required_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkflowRevisionPayloadError(
            f"Pull-request payload field '{field}' must be a positive integer."
        )
    return value


def _required_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise WorkflowRevisionPayloadError(
            f"Pull-request payload field '{field}' is invalid."
        )
    return value


def _repository_for_job(
    installation_id: int,
    repository_id: int,
) -> Repository:
    """Resolve an active repository belonging to the active installation."""
    try:
        installation = GitHubInstallation.objects.get(
            installation_id=installation_id,
            status=GitHubInstallation.Status.ACTIVE,
        )
    except GitHubInstallation.DoesNotExist as exc:
        raise WorkflowRevisionNotFoundError(
            "The GitHub installation is unavailable."
        ) from exc

    try:
        return Repository.objects.get(
            github_repo_id=repository_id,
            installation=installation,
            status=Repository.Status.ACTIVE,
        )
    except Repository.DoesNotExist as exc:
        raise WorkflowRevisionNotFoundError(
            "The repository is unavailable for this installation."
        ) from exc


def _decode_content(response: Any, path: str) -> str:
    """Decode a GitHub Contents API response containing base64 file content."""
    if not isinstance(response, dict):
        raise WorkflowRevisionPayloadError(
            f"GitHub returned an invalid response for workflow '{path}'."
        )

    content = response.get("content")
    encoding = response.get("encoding")

    if response.get("path") != path:
        raise WorkflowRevisionPayloadError(
            f"GitHub returned an unexpected path for workflow '{path}'."
        )

    if not isinstance(content, str) or encoding != "base64":
        raise WorkflowRevisionPayloadError(
            f"GitHub returned an unsupported content encoding for '{path}'."
        )

    try:
        decoded = base64.b64decode("".join(content.split()), validate=True)
        return decoded.decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise WorkflowRevisionPayloadError(
            f"GitHub returned invalid UTF-8 content for workflow '{path}'."
        ) from exc


class WorkflowRevisionRetriever:
    """Retrieve changed workflow files from exact pull-request revisions."""

    def __init__(self, client: GitHubAppClient | None = None):
        self._owns_client = client is None
        self.client = client if client is not None else GitHubAppClient.from_settings()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def retrieve(
        self,
        *,
        installation_id: int,
        repository_id: int,
        pull_request_number: int,
        base_sha: str,
        head_sha: str,
    ) -> list[WorkflowRevisionPair]:
        installation_id = _required_positive_int(installation_id, "installation_id")
        repository_id = _required_positive_int(repository_id, "repository_id")
        pull_request_number = _required_positive_int(
            pull_request_number, "pull_request_number"
        )
        base_sha = _required_sha(base_sha, "base_sha")
        head_sha = _required_sha(head_sha, "head_sha")

        repository = _repository_for_job(installation_id, repository_id)
        owner_repo = repository.full_name
        encoded_repo = quote(owner_repo, safe="/")

        try:
            remote_repository = self.client.request(
                "GET",
                f"/repos/{encoded_repo}",
                installation_id=installation_id,
            )
        except GitHubNotFoundError as exc:
            raise WorkflowRevisionNotFoundError(
                "GitHub could not find the configured repository."
            ) from exc

        if (
            not isinstance(remote_repository, dict)
            or remote_repository.get("id") != repository_id
            or remote_repository.get("full_name") != owner_repo
        ):
            raise WorkflowRevisionPayloadError(
                "GitHub repository identity did not match the stored repository."
            )

        changed_files = self._list_pull_request_files(
            owner_repo=owner_repo,
            installation_id=installation_id,
            pull_request_number=pull_request_number,
        )

        pairs = []

        for item in changed_files:
            path = item.get("filename")
            status = item.get("status")
            previous_path = item.get("previous_filename", path)

            if not isinstance(path, str):
                raise WorkflowRevisionPayloadError(
                    "GitHub returned a pull-request file without a valid filename."
                )

            current_is_workflow = is_workflow_path(path)
            previous_is_workflow = isinstance(previous_path, str) and is_workflow_path(
                previous_path
            )

            if not current_is_workflow and not previous_is_workflow:
                continue

            if status not in {"added", "modified", "removed", "renamed"}:
                raise WorkflowRevisionPayloadError(
                    "GitHub returned an unsupported workflow change status."
                )

            if status == "renamed" and not isinstance(previous_path, str):
                raise WorkflowRevisionPayloadError(
                    "GitHub returned a renamed file without its previous path."
                )

            before_yaml = ""
            after_yaml = ""

            if previous_is_workflow and status != "added":
                before_yaml = self._get_file_at_revision(
                    owner_repo=owner_repo,
                    installation_id=installation_id,
                    path=previous_path,
                    sha=base_sha,
                )

            if current_is_workflow and status != "removed":
                after_yaml = self._get_file_at_revision(
                    owner_repo=owner_repo,
                    installation_id=installation_id,
                    path=path,
                    sha=head_sha,
                )

            # Keep the established analysis path: use the current workflow path
            # when present, otherwise use the old path for a workflow removal.
            analysis_path = path if current_is_workflow else previous_path

            pairs.append(
                WorkflowRevisionPair(
                    path=analysis_path,
                    before_yaml=before_yaml,
                    after_yaml=after_yaml,
                    status=status,
                    previous_path=(previous_path if status == "renamed" else None),
                    current_path=path if status == "renamed" else None,
                )
            )

        return pairs

    def _list_pull_request_files(
        self,
        *,
        owner_repo: str,
        installation_id: int,
        pull_request_number: int,
    ) -> list[dict[str, Any]]:
        results = []
        page = 1
        encoded_repo = quote(owner_repo, safe="/")

        while True:
            response = self.client.request(
                "GET",
                f"/repos/{encoded_repo}/pulls/{pull_request_number}/files",
                installation_id=installation_id,
                params={"per_page": FILES_PER_PAGE, "page": page},
            )

            if not isinstance(response, list):
                raise WorkflowRevisionPayloadError(
                    "GitHub returned an invalid pull-request files response."
                )

            if any(not isinstance(item, dict) for item in response):
                raise WorkflowRevisionPayloadError(
                    "GitHub returned an invalid pull-request file entry."
                )

            results.extend(response)

            # At GitHub's file-list cap, completeness cannot be guaranteed.
            if len(results) >= MAX_PULL_REQUEST_FILES:
                raise WorkflowRevisionPayloadError(
                    "Pull request reached GitHub's file-list limit; "
                    "complete workflow discovery cannot be guaranteed."
                )

            if len(response) < FILES_PER_PAGE:
                break

            page += 1

        return results

    def _get_file_at_revision(
        self,
        *,
        owner_repo: str,
        installation_id: int,
        path: str,
        sha: str,
    ) -> str:
        encoded_repo = quote(owner_repo, safe="/")
        encoded_path = quote(path, safe="/")

        try:
            response = self.client.request(
                "GET",
                f"/repos/{encoded_repo}/contents/{encoded_path}",
                installation_id=installation_id,
                params={"ref": sha},
            )
        except GitHubNotFoundError as exc:
            raise WorkflowRevisionNotFoundError(
                f"Workflow '{path}' could not be retrieved at the requested revision."
            ) from exc

        return _decode_content(response, path)
