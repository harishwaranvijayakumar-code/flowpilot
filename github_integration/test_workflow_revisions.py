import base64

import pytest

from github_integration.client import GitHubNotFoundError
from github_integration.models import GitHubInstallation, Repository
from github_integration.workflow_revisions import (
    WorkflowRevisionNotFoundError,
    WorkflowRevisionPayloadError,
    WorkflowRevisionRetriever,
    is_workflow_path,
)

INSTALLATION_ID = 77
REPOSITORY_ID = 1234
REPOSITORY_NAME = "example/project"
BASE_SHA = "base-sha"
HEAD_SHA = "head-sha"


def encoded_file(path, content):
    return {
        "path": path,
        "encoding": "base64",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }


class FakeGitHubClient:
    def __init__(self, files=None, *, repository_response=None):
        self.files = files or {}
        self.repository_response = (
            repository_response
            if repository_response is not None
            else {"id": REPOSITORY_ID, "full_name": REPOSITORY_NAME}
        )
        self.calls = []
        self.pull_request_files = []
        self.pull_request_pages = {}
        self.missing_files = set()
        self.invalid_files_response = None

    def request(
        self,
        method,
        path,
        *,
        installation_id,
        params=None,
        json_body=None,
    ):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "installation_id": installation_id,
                "params": params,
                "json_body": json_body,
            }
        )

        if path == f"/repos/{REPOSITORY_NAME}":
            return self.repository_response

        if path == f"/repos/{REPOSITORY_NAME}/pulls/5/files":
            if self.invalid_files_response is not None:
                return self.invalid_files_response

            page = (params or {}).get("page", 1)
            return self.pull_request_pages.get(
                page,
                self.pull_request_files if page == 1 else [],
            )

        prefix = f"/repos/{REPOSITORY_NAME}/contents/"
        if path.startswith(prefix):
            file_path = path[len(prefix) :]
            sha = (params or {}).get("ref")
            key = (sha, file_path)

            if key in self.missing_files:
                raise GitHubNotFoundError("Missing test file.")

            if key not in self.files:
                raise AssertionError(f"Unexpected file request: {key}")

            return encoded_file(file_path, self.files[key])

        raise AssertionError(f"Unexpected API request: {method} {path}")

    def close(self):
        pass


@pytest.fixture
def repository(db):
    installation = GitHubInstallation.objects.create(
        installation_id=INSTALLATION_ID,
        account_login="example",
        account_type=GitHubInstallation.AccountType.ORGANIZATION,
        status=GitHubInstallation.Status.ACTIVE,
    )
    return Repository.objects.create(
        installation=installation,
        github_repo_id=REPOSITORY_ID,
        full_name=REPOSITORY_NAME,
        default_branch="main",
        html_url="https://github.com/example/project",
        is_private=True,
        status=Repository.Status.ACTIVE,
    )


def make_retriever(client):
    return WorkflowRevisionRetriever(client=client)


def retrieve(retriever):
    return retriever.retrieve(
        installation_id=INSTALLATION_ID,
        repository_id=REPOSITORY_ID,
        pull_request_number=5,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )


def content_calls(client):
    return [call for call in client.calls if "/contents/" in call["path"]]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".github/workflows/ci.yml", True),
        (".github/workflows/release.yaml", True),
        (".github/workflows/CI.YML", True),
        (".github/workflows/readme.md", False),
        ("docs/workflows/ci.yml", False),
        (".github/workflows/nested/", False),
        (".github/workflows", False),
    ],
)
def test_is_workflow_path(path, expected):
    assert is_workflow_path(path) is expected


@pytest.mark.django_db
def test_retrieves_modified_workflow_at_base_and_head(repository):
    path = ".github/workflows/ci.yml"
    client = FakeGitHubClient(
        files={
            (BASE_SHA, path): "name: Old CI\n",
            (HEAD_SHA, path): "name: New CI\n",
        }
    )
    client.pull_request_files = [{"filename": path, "status": "modified"}]

    results = retrieve(make_retriever(client))

    assert len(results) == 1
    assert results[0].path == path
    assert results[0].before_yaml == "name: Old CI\n"
    assert results[0].after_yaml == "name: New CI\n"
    assert results[0].status == "modified"
    assert [call["params"]["ref"] for call in content_calls(client)] == [
        BASE_SHA,
        HEAD_SHA,
    ]


@pytest.mark.django_db
def test_added_workflow_only_fetches_head_revision(repository):
    path = ".github/workflows/new.yml"
    client = FakeGitHubClient(files={(HEAD_SHA, path): "name: New workflow\n"})
    client.pull_request_files = [{"filename": path, "status": "added"}]

    results = retrieve(make_retriever(client))

    assert len(results) == 1
    assert results[0].before_yaml == ""
    assert results[0].after_yaml == "name: New workflow\n"
    assert results[0].status == "added"
    assert [call["params"]["ref"] for call in content_calls(client)] == [HEAD_SHA]


@pytest.mark.django_db
def test_removed_workflow_only_fetches_base_revision(repository):
    path = ".github/workflows/old.yml"
    client = FakeGitHubClient(files={(BASE_SHA, path): "name: Old workflow\n"})
    client.pull_request_files = [{"filename": path, "status": "removed"}]

    results = retrieve(make_retriever(client))

    assert len(results) == 1
    assert results[0].before_yaml == "name: Old workflow\n"
    assert results[0].after_yaml == ""
    assert results[0].status == "removed"
    assert [call["params"]["ref"] for call in content_calls(client)] == [BASE_SHA]


@pytest.mark.django_db
def test_ignores_non_workflow_changes(repository):
    client = FakeGitHubClient()
    client.pull_request_files = [
        {"filename": "README.md", "status": "modified"},
        {"filename": "src/app.py", "status": "modified"},
    ]

    assert retrieve(make_retriever(client)) == []
    assert content_calls(client) == []


@pytest.mark.django_db
def test_renaming_workflow_out_of_directory_is_treated_as_removal(repository):
    old_path = ".github/workflows/ci.yml"
    new_path = "docs/ci.yml"
    client = FakeGitHubClient(files={(BASE_SHA, old_path): "name: CI\n"})
    client.pull_request_files = [
        {
            "filename": new_path,
            "previous_filename": old_path,
            "status": "renamed",
        }
    ]

    results = retrieve(make_retriever(client))

    assert len(results) == 1
    assert results[0].path == old_path
    assert results[0].before_yaml == "name: CI\n"
    assert results[0].after_yaml == ""
    assert results[0].status == "renamed"


@pytest.mark.django_db
def test_renaming_file_into_workflow_directory_is_treated_as_addition(repository):
    old_path = "docs/ci.yml"
    new_path = ".github/workflows/ci.yml"
    client = FakeGitHubClient(files={(HEAD_SHA, new_path): "name: CI\n"})
    client.pull_request_files = [
        {
            "filename": new_path,
            "previous_filename": old_path,
            "status": "renamed",
        }
    ]

    results = retrieve(make_retriever(client))

    assert len(results) == 1
    assert results[0].path == new_path
    assert results[0].before_yaml == ""
    assert results[0].after_yaml == "name: CI\n"


@pytest.mark.django_db
def test_rejects_repository_identity_mismatch(repository):
    client = FakeGitHubClient(
        repository_response={"id": 9999, "full_name": REPOSITORY_NAME}
    )

    with pytest.raises(WorkflowRevisionPayloadError):
        retrieve(make_retriever(client))

    assert not any("/pulls/" in call["path"] for call in client.calls)


@pytest.mark.django_db
def test_rejects_unavailable_installation(repository):
    repository.installation.status = GitHubInstallation.Status.SUSPENDED
    repository.installation.save(update_fields=["status", "updated_at"])
    client = FakeGitHubClient()

    with pytest.raises(WorkflowRevisionNotFoundError):
        retrieve(make_retriever(client))

    assert client.calls == []


@pytest.mark.django_db
def test_rejects_inactive_repository(repository):
    repository.status = Repository.Status.DISABLED
    repository.save(update_fields=["status", "updated_at"])
    client = FakeGitHubClient()

    with pytest.raises(WorkflowRevisionNotFoundError):
        retrieve(make_retriever(client))

    assert client.calls == []


@pytest.mark.django_db
def test_missing_modified_workflow_revision_is_not_silently_ignored(repository):
    path = ".github/workflows/ci.yml"
    client = FakeGitHubClient(files={(HEAD_SHA, path): "name: CI\n"})
    client.pull_request_files = [{"filename": path, "status": "modified"}]
    client.missing_files.add((BASE_SHA, path))

    with pytest.raises(WorkflowRevisionNotFoundError):
        retrieve(make_retriever(client))


@pytest.mark.django_db
def test_rejects_malformed_pull_request_files_response(repository):
    client = FakeGitHubClient()
    client.invalid_files_response = {"message": "unexpected shape"}

    with pytest.raises(WorkflowRevisionPayloadError):
        retrieve(make_retriever(client))


@pytest.mark.django_db
def test_rejects_malformed_base64_content(repository):
    path = ".github/workflows/ci.yml"
    client = FakeGitHubClient()
    client.pull_request_files = [{"filename": path, "status": "added"}]

    original_request = client.request

    def malformed_request(
        method,
        request_path,
        *,
        installation_id,
        params=None,
        json_body=None,
    ):
        if request_path.endswith("/contents/" + path):
            return {
                "path": path,
                "encoding": "base64",
                "content": "%%%not-base64%%%",
            }

        return original_request(
            method,
            request_path,
            installation_id=installation_id,
            params=params,
            json_body=json_body,
        )

    client.request = malformed_request

    with pytest.raises(WorkflowRevisionPayloadError):
        retrieve(make_retriever(client))


@pytest.mark.django_db
def test_paginates_pull_request_files(repository):
    first_page = [
        {"filename": f"docs/file-{index}.txt", "status": "modified"}
        for index in range(100)
    ]
    client = FakeGitHubClient()
    client.pull_request_pages = {
        1: first_page,
        2: [{"filename": "README.md", "status": "modified"}],
    }

    assert retrieve(make_retriever(client)) == []

    page_calls = [call for call in client.calls if "/pulls/5/files" in call["path"]]
    assert [call["params"]["page"] for call in page_calls] == [1, 2]


@pytest.mark.django_db
def test_rejects_invalid_revision_inputs(repository):
    client = FakeGitHubClient()
    retriever = make_retriever(client)

    with pytest.raises(WorkflowRevisionPayloadError):
        retriever.retrieve(
            installation_id=True,
            repository_id=REPOSITORY_ID,
            pull_request_number=5,
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
        )

    assert client.calls == []
