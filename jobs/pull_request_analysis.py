from typing import Any

from github_integration.workflow_revisions import WorkflowRevisionRetriever
from jobs.analysis_handler import (
    WorkflowAnalysisOutput,
    analyze_workflow_payload,
)


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Pull-request job requires a positive integer '{field}'.")
    return value


def _required_sha(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"Pull-request job requires a valid '{field}'.")
    return value


def analyze_pull_request_payload(
    payload: dict[str, Any],
) -> WorkflowAnalysisOutput:
    """Analyze every changed workflow in a pull request."""
    if not isinstance(payload, dict):
        raise ValueError("Pull-request analysis payload must be an object.")

    installation_id = _required_positive_int(payload, "installation_id")
    repository_id = _required_positive_int(payload, "repository_id")
    pull_request_number = _required_positive_int(payload, "pull_request_number")
    base_sha = _required_sha(payload, "base_sha")
    head_sha = _required_sha(payload, "head_sha")

    with WorkflowRevisionRetriever() as retriever:
        revisions = retriever.retrieve(
            installation_id=installation_id,
            repository_id=repository_id,
            pull_request_number=pull_request_number,
            base_sha=base_sha,
            head_sha=head_sha,
        )

    findings: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    valid = True

    for revision in revisions:
        result = analyze_workflow_payload(
            {
                "before_yaml": revision.before_yaml,
                "after_yaml": revision.after_yaml,
            }
        )
        valid = valid and result.valid

        file_metadata = {
            "workflow_path": revision.path,
            "change_status": revision.status,
            "previous_path": revision.previous_path,
            "current_path": revision.current_path,
        }

        findings.extend({**finding, **file_metadata} for finding in result.findings)
        diagnostics.extend(
            {**diagnostic, **file_metadata} for diagnostic in result.diagnostics
        )

    return WorkflowAnalysisOutput(
        valid=valid,
        findings=findings,
        diagnostics=diagnostics,
    )
