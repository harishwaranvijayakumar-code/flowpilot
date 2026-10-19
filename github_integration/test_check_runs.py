from types import SimpleNamespace
from unittest.mock import Mock, patch

from github_integration.check_runs import (
    CHECK_RUN_NAME,
    CheckRunPublisher,
    build_check_run_output,
)


def test_output_includes_findings_paths_and_recommendations():
    output = build_check_run_output(
        valid=True,
        findings=[
            {
                "rule_id": "permissions.write_scope_added",
                "severity": "high",
                "title": "Write permission added",
                "workflow_path": ".github/workflows/build.yml",
                "change_status": "modified",
                "description": "A write permission was added.",
                "recommendation": "Use least privilege.",
            }
        ],
        diagnostics=[],
    )

    assert output["title"] == "Analysis complete: 1 finding(s)"
    assert r"permissions\.write\_scope\_added" in output["summary"]
    assert r"\.github/workflows/build\.yml" in output["summary"]
    assert r"Use least privilege\." in output["summary"]


def test_invalid_analysis_is_reported_as_failure():
    output = build_check_run_output(
        valid=False,
        findings=[],
        diagnostics=[
            {
                "code": "invalid_yaml",
                "message": "Invalid workflow YAML",
                "workflow_path": ".github/workflows/build.yml",
            }
        ],
    )

    assert "validation failed" in output["title"].lower()
    assert r"invalid\_yaml" in output["summary"]


def test_output_escapes_untrusted_markdown():
    output = build_check_run_output(
        valid=True,
        findings=[
            {
                "rule_id": "[spoof](https://example.invalid)",
                "title": "**injected heading**",
                "workflow_path": ".github/workflows/build.yml",
                "description": "A finding with untrusted Markdown.",
                "recommendation": "Review this finding.",
            }
        ],
        diagnostics=[],
    )

    assert r"\[" in output["summary"]
    assert r"\*" in output["summary"]
    assert "[spoof](" not in output["summary"]
    assert "**injected heading**" not in output["summary"]


def test_publisher_creates_check_run_when_no_matching_run_exists():
    client = Mock()
    client.request.side_effect = [
        {"head": {"sha": "abc123"}},
        {"check_runs": []},
        {"id": 123, "name": CHECK_RUN_NAME},
    ]

    repository = SimpleNamespace(full_name="example/project")

    with patch(
        "github_integration.check_runs._repository_for_job",
        return_value=repository,
    ):
        with CheckRunPublisher(client) as publisher:
            result = publisher.publish(
                installation_id=10,
                repository_id=20,
                pull_request_number=7,
                head_sha="abc123",
                analysis_job_id="analysis-job-id",
                valid=True,
                findings=[],
                diagnostics=[],
            )

    assert result["id"] == 123
    assert client.request.call_count == 3

    pull_request_call = client.request.call_args_list[0]
    assert pull_request_call.args[:2] == (
        "GET",
        "/repos/example/project/pulls/7",
    )

    create_call = client.request.call_args_list[2]
    assert create_call.args[:2] == (
        "POST",
        "/repos/example/project/check-runs",
    )

    body = create_call.kwargs["json_body"]
    assert body["head_sha"] == "abc123"
    assert body["name"] == CHECK_RUN_NAME
    assert body["status"] == "completed"
    assert body["conclusion"] == "success"
    assert body["external_id"] == "flowpilot:pr:20:7:abc123"


def test_publisher_updates_existing_matching_check_run():
    client = Mock()
    client.request.side_effect = [
        {"head": {"sha": "abc123"}},
        {
            "check_runs": [
                {
                    "id": 123,
                    "name": CHECK_RUN_NAME,
                    "external_id": "flowpilot:pr:20:7:abc123",
                }
            ]
        },
        {"id": 123, "name": CHECK_RUN_NAME},
    ]

    repository = SimpleNamespace(full_name="example/project")

    with patch(
        "github_integration.check_runs._repository_for_job",
        return_value=repository,
    ):
        with CheckRunPublisher(client) as publisher:
            result = publisher.publish(
                installation_id=10,
                repository_id=20,
                pull_request_number=7,
                head_sha="abc123",
                analysis_job_id="analysis-job-id",
                valid=True,
                findings=[],
                diagnostics=[],
            )

    assert result["id"] == 123
    assert client.request.call_count == 3

    update_call = client.request.call_args_list[2]
    assert update_call.args[:2] == (
        "PATCH",
        "/repos/example/project/check-runs/123",
    )


def test_publisher_skips_check_run_when_pull_request_head_changed():
    client = Mock()
    client.request.return_value = {"head": {"sha": "new-head"}}

    repository = SimpleNamespace(full_name="example/project")

    with patch(
        "github_integration.check_runs._repository_for_job",
        return_value=repository,
    ):
        with CheckRunPublisher(client) as publisher:
            result = publisher.publish(
                installation_id=10,
                repository_id=20,
                pull_request_number=7,
                head_sha="old-head",
                analysis_job_id="analysis-job-id",
                valid=True,
                findings=[],
                diagnostics=[],
            )

    assert result == {
        "skipped": True,
        "reason": "pull_request_head_changed",
    }
    assert client.request.call_count == 1
