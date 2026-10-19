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
    assert "permissions.write_scope_added" in output["summary"]
    assert ".github/workflows/build.yml" in output["summary"]
    assert "Use least privilege." in output["summary"]


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

    assert "validation failed" in output["title"]
    assert "invalid_yaml" in output["summary"]


def test_publisher_creates_check_run_when_no_matching_run_exists():
    client = Mock()
    client.request.side_effect = [
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
    assert client.request.call_count == 2
    create_call = client.request.call_args_list[1]
    assert create_call.args[:2] == (
        "POST",
        "/repos/example/project/check-runs",
    )
    body = create_call.kwargs["json_body"]
    assert body["head_sha"] == "abc123"
    assert body["name"] == CHECK_RUN_NAME
    assert body["status"] == "completed"
    assert body["conclusion"] == "success"


def test_publisher_updates_existing_matching_check_run():
    client = Mock()
    client.request.side_effect = [
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
            publisher.publish(
                installation_id=10,
                repository_id=20,
                pull_request_number=7,
                head_sha="abc123",
                analysis_job_id="analysis-job-id",
                valid=True,
                findings=[],
                diagnostics=[],
            )

    update_call = client.request.call_args_list[1]
    assert update_call.args[:2] == (
        "PATCH",
        "/repos/example/project/check-runs/123",
    )
