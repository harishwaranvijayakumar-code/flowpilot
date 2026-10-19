from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jobs.analysis_handler import WorkflowAnalysisOutput
from jobs.management.commands.run_worker import JOB_HANDLERS
from jobs.pull_request_analysis import analyze_pull_request_payload


@pytest.fixture
def valid_payload():
    return {
        "installation_id": 10,
        "repository_id": 20,
        "pull_request_number": 7,
        "base_sha": "base-commit-sha",
        "head_sha": "head-commit-sha",
    }


def revision(
    path,
    before_yaml="before",
    after_yaml="after",
    status="modified",
    previous_path=None,
    current_path=None,
):
    return SimpleNamespace(
        path=path,
        before_yaml=before_yaml,
        after_yaml=after_yaml,
        status=status,
        previous_path=previous_path,
        current_path=current_path,
    )


def test_analyzes_every_changed_workflow_and_preserves_file_metadata(valid_payload):
    revisions = [
        revision(".github/workflows/build.yml"),
        revision(
            ".github/workflows/release.yaml",
            status="renamed",
            previous_path=".github/workflows/publish.yml",
            current_path=".github/workflows/release.yaml",
        ),
    ]

    outputs = [
        WorkflowAnalysisOutput(
            valid=True,
            findings=[{"rule_id": "actions.reference_added"}],
            diagnostics=[],
        ),
        WorkflowAnalysisOutput(
            valid=False,
            findings=[],
            diagnostics=[{"code": "invalid_yaml", "message": "Invalid YAML"}],
        ),
    ]

    with (
        patch(
            "jobs.pull_request_analysis.WorkflowRevisionRetriever"
        ) as retriever_class,
        patch(
            "jobs.pull_request_analysis.analyze_workflow_payload",
            side_effect=outputs,
        ) as analyze,
    ):
        retriever_class.return_value.__enter__.return_value.retrieve.return_value = (
            revisions
        )

        result = analyze_pull_request_payload(valid_payload)

    assert analyze.call_count == 2
    assert result.valid is False
    assert result.findings == [
        {
            "rule_id": "actions.reference_added",
            "workflow_path": ".github/workflows/build.yml",
            "change_status": "modified",
            "previous_path": None,
            "current_path": None,
        }
    ]
    assert result.diagnostics == [
        {
            "code": "invalid_yaml",
            "message": "Invalid YAML",
            "workflow_path": ".github/workflows/release.yaml",
            "change_status": "renamed",
            "previous_path": ".github/workflows/publish.yml",
            "current_path": ".github/workflows/release.yaml",
        }
    ]


def test_pull_request_without_workflow_changes_is_successful(valid_payload):
    with patch(
        "jobs.pull_request_analysis.WorkflowRevisionRetriever"
    ) as retriever_class:
        retriever_class.return_value.__enter__.return_value.retrieve.return_value = []

        result = analyze_pull_request_payload(valid_payload)

    assert result.valid is True
    assert result.findings == []
    assert result.diagnostics == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installation_id", True),
        ("repository_id", 0),
        ("pull_request_number", "7"),
        ("base_sha", ""),
        ("head_sha", None),
    ],
)
def test_rejects_invalid_pull_request_payload(valid_payload, field, value):
    valid_payload[field] = value

    with pytest.raises(ValueError):
        analyze_pull_request_payload(valid_payload)


def test_retrieval_failure_propagates_for_worker_retry(valid_payload):
    with patch(
        "jobs.pull_request_analysis.WorkflowRevisionRetriever"
    ) as retriever_class:
        retriever_class.return_value.__enter__.return_value.retrieve.side_effect = (
            RuntimeError("GitHub API temporarily unavailable")
        )

        with pytest.raises(
            RuntimeError,
            match="GitHub API temporarily unavailable",
        ):
            analyze_pull_request_payload(valid_payload)


def test_worker_registers_pull_request_analysis_handler():
    assert "workflow_analysis" in JOB_HANDLERS
    assert "analyze_pull_request" in JOB_HANDLERS
