from typing import Any
from urllib.parse import quote

from django.utils import timezone

from github_integration.client import (
    GitHubAppClient,
    GitHubResponseError,
)
from github_integration.workflow_revisions import _repository_for_job

CHECK_RUN_NAME = "FlowPilot Workflow Analysis"
CHECK_RUN_VERSION = 1


def _markdown_text(value: Any) -> str:
    """Keep untrusted values from breaking inline Markdown formatting."""
    return str(value).replace("`", "\\`").replace("\r", " ").replace("\n", " ")


def build_check_run_output(
    *,
    valid: bool,
    findings: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> dict[str, str]:
    """Create the title, summary, and details for a GitHub Check Run."""
    if valid:
        title = f"Analysis complete: {len(findings)} finding(s)"
    else:
        title = "Analysis incomplete: workflow validation failed"

    lines = [
        "## FlowPilot workflow analysis",
        "",
        "- **Workflows analyzed:** results are available in the persisted analysis.",
        f"- **Findings:** {len(findings)}",
        f"- **Diagnostics:** {len(diagnostics)}",
        "",
    ]

    if findings:
        lines.extend(["### Findings", ""])
        for index, finding in enumerate(findings, start=1):
            severity = _markdown_text(finding.get("severity", "unspecified"))
            rule_id = _markdown_text(finding.get("rule_id", "unknown"))
            finding_title = _markdown_text(finding.get("title", rule_id))
            path = _markdown_text(finding.get("workflow_path", "unknown workflow"))
            status = _markdown_text(finding.get("change_status", "unknown"))

            lines.append(f"{index}. **[{severity}] {finding_title}**")
            lines.append(f"   - Rule: `{rule_id}`")
            lines.append(f"   - Workflow: `{path}` ({status})")

            previous_path = finding.get("previous_path")
            current_path = finding.get("current_path")
            if previous_path:
                lines.append(f"   - Previous path: `{_markdown_text(previous_path)}`")
            if current_path:
                lines.append(f"   - Current path: `{_markdown_text(current_path)}`")

            description = finding.get("description")
            recommendation = finding.get("recommendation")
            if description:
                lines.append(f"   - {_markdown_text(description)}")
            if recommendation:
                lines.append(f"   - Recommendation: {_markdown_text(recommendation)}")
            lines.append("")

    if diagnostics:
        lines.extend(["### Diagnostics", ""])
        for diagnostic in diagnostics:
            code = _markdown_text(diagnostic.get("code", "unknown"))
            message = _markdown_text(diagnostic.get("message", "No message"))
            path = _markdown_text(diagnostic.get("workflow_path", "unknown workflow"))
            lines.append(f"- **{code}** in `{path}`: {message}")

    if not findings and not diagnostics:
        lines.append("No workflow findings or diagnostics were reported.")

    return {
        "title": title[:255],
        "summary": "\n".join(lines)[:65000],
    }


class CheckRunPublisher:
    """Idempotently create or update FlowPilot's Check Run for a commit."""

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
        encoded_repo = quote(repository.full_name, safe="/")
        external_id = f"flowpilot:pr:{repository_id}:{pull_request_number}:{head_sha}"
        output = build_check_run_output(
            valid=valid,
            findings=findings,
            diagnostics=diagnostics,
        )
        now = timezone.now().isoformat()

        body = {
            "name": CHECK_RUN_NAME,
            "head_sha": head_sha,
            "external_id": external_id,
            "status": "completed",
            "conclusion": "success" if valid else "failure",
            "completed_at": now,
            "output": output,
        }

        response = self.client.request(
            "GET",
            f"/repos/{encoded_repo}/commits/{quote(head_sha, safe='')}/check-runs",
            installation_id=installation_id,
            params={
                "check_name": CHECK_RUN_NAME,
                "per_page": 100,
            },
        )

        if not isinstance(response, dict) or not isinstance(
            response.get("check_runs"), list
        ):
            raise GitHubResponseError("GitHub returned an invalid Check Runs listing.")

        existing = next(
            (
                item
                for item in response["check_runs"]
                if isinstance(item, dict)
                and item.get("external_id") == external_id
                and item.get("name") == CHECK_RUN_NAME
            ),
            None,
        )

        if existing is not None:
            check_run_id = existing.get("id")
            if (
                isinstance(check_run_id, bool)
                or not isinstance(check_run_id, int)
                or check_run_id <= 0
            ):
                raise GitHubResponseError(
                    "GitHub returned a Check Run with an invalid ID."
                )

            return self.client.request(
                "PATCH",
                f"/repos/{encoded_repo}/check-runs/{check_run_id}",
                installation_id=installation_id,
                json_body=body,
            )

        return self.client.request(
            "POST",
            f"/repos/{encoded_repo}/check-runs",
            installation_id=installation_id,
            json_body=body,
        )
