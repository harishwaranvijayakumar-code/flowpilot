import re
from typing import Any
from urllib.parse import quote

from github_integration.client import GitHubAppClient, GitHubResponseError
from github_integration.workflow_revisions import _repository_for_job

CHECK_RUN_NAME = "FlowPilot Workflow Analysis"
MARKDOWN_SPECIAL_CHARS = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")


def _markdown_text(value: Any) -> str:
    """Escape untrusted text before inserting it into GitHub Markdown."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return MARKDOWN_SPECIAL_CHARS.sub(r"\\\1", text)


def build_check_run_output(
    *,
    valid: bool,
    findings: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> dict[str, str]:
    """Build a bounded, Markdown-safe summary for a GitHub Check Run."""
    finding_count = len(findings)
    diagnostic_count = len(diagnostics)

    if valid:
        title = f"Analysis complete: {finding_count} finding(s)"
    else:
        title = "Workflow validation failed"

    lines = [
        "## FlowPilot workflow analysis",
        "",
        f"- **Result:** {'Valid' if valid else 'Invalid'}",
        f"- **Findings:** {finding_count}",
        f"- **Diagnostics:** {diagnostic_count}",
    ]

    if findings:
        lines.extend(["", "## Findings"])
        for finding in findings:
            rule_id = _markdown_text(finding.get("rule_id", "unknown"))
            severity = _markdown_text(finding.get("severity", "unknown"))
            finding_title = _markdown_text(finding.get("title", "Untitled finding"))
            workflow_path = _markdown_text(finding.get("workflow_path", "unknown"))
            change_status = _markdown_text(finding.get("change_status", "unknown"))
            description = _markdown_text(finding.get("description", ""))
            recommendation = _markdown_text(finding.get("recommendation", ""))

            lines.extend(
                [
                    "",
                    f"### {finding_title}",
                    f"- **Rule:** `{rule_id}`",
                    f"- **Severity:** {severity}",
                    f"- **Workflow:** `{workflow_path}`",
                    f"- **Change:** {change_status}",
                ]
            )
            if description:
                lines.extend(["", description])
            if recommendation:
                lines.extend(["", f"**Recommendation:** {recommendation}"])

    if diagnostics:
        lines.extend(["", "## Diagnostics"])
        for diagnostic in diagnostics:
            code = _markdown_text(diagnostic.get("code", "unknown"))
            message = _markdown_text(diagnostic.get("message", ""))
            workflow_path = _markdown_text(diagnostic.get("workflow_path", "unknown"))
            lines.extend(
                [
                    "",
                    f"- **Code:** `{code}`",
                    f"  - **Workflow:** `{workflow_path}`",
                    f"  - **Message:** {message}",
                ]
            )

    summary = "\n".join(lines)
    if len(summary) > 65000:
        summary = summary[:64980] + "\n\n[Output truncated.]"

    return {"title": title, "summary": summary}


class CheckRunPublisher:
    def __init__(self, client: GitHubAppClient) -> None:
        self.client = client

    def __enter__(self) -> "CheckRunPublisher":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def publish(
        self,
        *,
        installation_id: int,
        repository_id: int,
        pull_request_number: int,
        head_sha: str,
        analysis_job_id: str,
        valid: bool,
        findings: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        repository = _repository_for_job(installation_id, repository_id)
        repository_name = repository.full_name
        encoded_repository_name = quote(repository_name, safe="/")
        encoded_sha = quote(head_sha, safe="")

        # Do not publish a result for a commit that is no longer the PR head.
        pull_request = self.client.request(
            "GET",
            (f"/repos/{encoded_repository_name}/pulls/{pull_request_number}"),
            installation_id=installation_id,
        )
        current_head_sha = pull_request.get("head", {}).get("sha")
        if not isinstance(current_head_sha, str) or not current_head_sha:
            raise GitHubResponseError(
                "GitHub returned a pull request without a valid head SHA."
            )

        if current_head_sha != head_sha:
            return {
                "skipped": True,
                "reason": "pull_request_head_changed",
            }

        external_id = f"flowpilot:pr:{repository_id}:{pull_request_number}:{head_sha}"
        output = build_check_run_output(
            valid=valid,
            findings=findings,
            diagnostics=diagnostics,
        )

        check_runs_response = self.client.request(
            "GET",
            f"/repos/{encoded_repository_name}/commits/{encoded_sha}/check-runs",
            installation_id=installation_id,
            params={
                "check_name": CHECK_RUN_NAME,
                "per_page": 100,
            },
        )

        matching_run = next(
            (
                check_run
                for check_run in check_runs_response.get("check_runs", [])
                if check_run.get("external_id") == external_id
            ),
            None,
        )

        body = {
            "name": CHECK_RUN_NAME,
            "head_sha": head_sha,
            "external_id": external_id,
            "status": "completed",
            "conclusion": "success" if valid else "failure",
            "output": {
                "title": output["title"],
                "summary": output["summary"],
            },
        }

        if matching_run is not None:
            check_run_id = matching_run.get("id")
            if not isinstance(check_run_id, int) or isinstance(check_run_id, bool):
                raise GitHubResponseError(
                    "GitHub returned a matching Check Run without a valid ID."
                )

            return self.client.request(
                "PATCH",
                (f"/repos/{encoded_repository_name}/check-runs/{check_run_id}"),
                installation_id=installation_id,
                json_body=body,
            )

        return self.client.request(
            "POST",
            f"/repos/{encoded_repository_name}/check-runs",
            installation_id=installation_id,
            json_body=body,
        )
