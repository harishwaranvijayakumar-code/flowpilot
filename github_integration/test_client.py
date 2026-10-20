from unittest.mock import Mock

import pytest

from github_integration.client import (
    GitHubAppClient,
    GitHubConfigurationError,
    GitHubResponseError,
)


def make_repository(repository_id):
    return {
        "id": repository_id,
        "full_name": f"flowpilot/repo-{repository_id}",
        "html_url": f"https://github.com/flowpilot/repo-{repository_id}",
        "default_branch": "main",
        "private": True,
    }


def make_client():
    return GitHubAppClient(
        app_id="12345",
        private_key="unused-in-this-test",
        http_client=Mock(),
    )


def test_lists_repositories_from_one_page():
    client = make_client()
    client.request = Mock(
        return_value={
            "total_count": 2,
            "repositories": [
                make_repository(101),
                make_repository(102),
            ],
        }
    )

    repositories = client.list_installation_repositories(9876)

    assert [repo["id"] for repo in repositories] == [101, 102]
    client.request.assert_called_once_with(
        "GET",
        "/installation/repositories",
        installation_id=9876,
        params={"per_page": 100, "page": 1},
    )


def test_lists_repositories_across_pages():
    client = make_client()
    client.request = Mock(
        side_effect=[
            {
                "total_count": 2,
                "repositories": [make_repository(101)],
            },
            {
                "total_count": 2,
                "repositories": [make_repository(102)],
            },
        ]
    )

    repositories = client.list_installation_repositories(9876)

    assert [repo["id"] for repo in repositories] == [101, 102]
    assert client.request.call_count == 2
    assert client.request.call_args_list[1].kwargs["params"] == {
        "per_page": 100,
        "page": 2,
    }


@pytest.mark.parametrize("installation_id", [0, -1, True, "123"])
def test_rejects_invalid_installation_id(installation_id):
    client = make_client()
    client.request = Mock()

    with pytest.raises(GitHubConfigurationError):
        client.list_installation_repositories(installation_id)

    client.request.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {"total_count": -1, "repositories": []},
        {"total_count": True, "repositories": []},
        {"total_count": 1, "repositories": {}},
        {"total_count": 1, "repositories": [None]},
        {"total_count": 1, "repositories": [{"id": 0}]},
        {
            "total_count": 1,
            "repositories": [
                {
                    "id": 101,
                    "full_name": "flowpilot/repo",
                    "html_url": "http://github.com/flowpilot/repo",
                }
            ],
        },
    ],
)
def test_rejects_malformed_repository_responses(response):
    client = make_client()
    client.request = Mock(return_value=response)

    with pytest.raises(GitHubResponseError):
        client.list_installation_repositories(9876)


def test_rejects_incomplete_pagination():
    client = make_client()
    client.request = Mock(
        side_effect=[
            {
                "total_count": 2,
                "repositories": [make_repository(101)],
            },
            {
                "total_count": 2,
                "repositories": [],
            },
        ]
    )

    with pytest.raises(GitHubResponseError):
        client.list_installation_repositories(9876)
