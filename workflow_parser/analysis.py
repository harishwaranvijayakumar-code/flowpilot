from dataclasses import asdict, dataclass
from typing import Any, Literal

from workflow_parser.comparison import ComparisonResult, WorkflowChange
from workflow_parser.parser import Diagnostic, SourceLocation

Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class Finding:
    """One deterministic, evidence-backed workflow risk finding."""

    rule_id: str
    severity: Severity
    title: str
    description: str
    evidence: tuple[WorkflowChange, ...]
    location: SourceLocation | None
    recommendation: str
    confidence: Confidence

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "evidence": [
                {
                    "category": item.category,
                    "change_type": item.change_type,
                    "path": item.path,
                    "before": item.before,
                    "after": item.after,
                    "message": item.message,
                    "source_location": (
                        asdict(item.source_location)
                        if item.source_location is not None
                        else None
                    ),
                }
                for item in self.evidence
            ],
            "location": asdict(self.location) if self.location is not None else None,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class AnalysisResult:
    """Deterministic analysis output."""

    valid: bool
    findings: tuple[Finding, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "valid": self.valid,
            "findings": [finding.to_dict() for finding in self.findings],
            "diagnostics": [
                {
                    "code": item.code,
                    "message": item.message,
                    "line": item.line,
                    "column": item.column,
                    "severity": item.severity,
                }
                for item in self.diagnostics
            ],
        }


class WorkflowAnalyzer:
    """Apply deterministic rules to workflow comparison results."""

    def analyze(self, comparison: ComparisonResult) -> AnalysisResult:
        if not comparison.valid:
            diagnostics = tuple(
                sorted(
                    comparison.diagnostics,
                    key=lambda item: (
                        item.code,
                        item.message,
                        item.line or 0,
                        item.column or 0,
                    ),
                )
            )
            if not diagnostics:
                diagnostics = (
                    Diagnostic(
                        code="invalid_comparison",
                        message=(
                            "A valid workflow comparison is required before analysis."
                        ),
                    ),
                )

            return AnalysisResult(valid=False, diagnostics=diagnostics)

        findings: list[Finding] = []

        for change in comparison.changes:
            findings.extend(self._analyze_change(change))

        ordered = tuple(
            sorted(
                self._deduplicate(findings),
                key=lambda finding: (
                    finding.rule_id,
                    finding.location.line if finding.location else 0,
                    finding.location.column if finding.location else 0,
                    finding.title,
                    tuple(item.path for item in finding.evidence),
                ),
            )
        )

        return AnalysisResult(valid=True, findings=ordered)

    def _analyze_change(self, change: WorkflowChange) -> list[Finding]:
        findings: list[Finding] = []

        if change.category == "permission":
            findings.extend(self._analyze_permissions(change))

        if (
            change.category == "trigger"
            and change.change_type == "added"
            and change.path == "on.pull_request_target"
        ):
            findings.append(
                self._finding(
                    change=change,
                    rule_id="triggers.pull_request_target_added",
                    severity="high",
                    title="Privileged pull-request trigger added",
                    description=(
                        "The workflow now uses pull_request_target. "
                        "This event can run with elevated repository context; "
                        "review any handling of untrusted pull-request content."
                    ),
                    recommendation=(
                        "Review permissions, checkout behavior, and any use of "
                        "pull-request-controlled data before merging."
                    ),
                    confidence="high",
                )
            )

        if (
            change.category == "step"
            and change.change_type == "added"
            and isinstance(change.after, dict)
            and isinstance(change.after.get("uses"), str)
            and change.after["uses"].strip()
        ):
            findings.append(
                self._finding(
                    change=change,
                    rule_id="actions.reference_added",
                    severity="medium",
                    title="Action reference added",
                    description=(
                        "A new workflow step invokes a GitHub Action. "
                        "The addition changes which external action code "
                        "the workflow may execute."
                    ),
                    recommendation=(
                        "Review the action owner, repository, version or commit "
                        "SHA, and required permissions before merging."
                    ),
                    confidence="high",
                )
            )

        if (
            change.category == "step"
            and change.path.endswith(".uses")
            and change.change_type == "modified"
        ):
            findings.append(
                self._finding(
                    change=change,
                    rule_id="actions.reference_changed",
                    severity="medium",
                    title="Action reference changed",
                    description=(
                        "A GitHub Actions reference changed. This may alter "
                        "the code executed by the workflow, but the change "
                        "alone does not establish that the new reference is unsafe."
                    ),
                    recommendation=(
                        "Review the new action owner, repository, version or "
                        "commit SHA, and its permissions requirements."
                    ),
                    confidence="high",
                )
            )

        return findings

    def _analyze_permissions(self, change: WorkflowChange) -> list[Finding]:
        before = change.before
        after = change.after
        findings: list[Finding] = []

        if after == "write-all" and before != "write-all":
            findings.append(
                self._finding(
                    change=change,
                    rule_id="permissions.write_all_enabled",
                    severity="high",
                    title="All workflow permissions set to write",
                    description=(
                        "The workflow explicitly sets permissions to write-all, "
                        "granting write access across supported permission scopes."
                    ),
                    recommendation=(
                        "Use the least-privilege permission scopes required "
                        "by the workflow."
                    ),
                    confidence="high",
                )
            )
            return findings

        old_permissions = self._permission_mapping(before)
        new_permissions = self._permission_mapping(after)

        # Do not infer effective permissions from omitted or shorthand values.
        # These require GitHub's permission defaults and inheritance semantics.
        if old_permissions is None or new_permissions is None:
            return findings

        write_scopes = sorted(
            scope
            for scope, value in new_permissions.items()
            if value == "write" and old_permissions.get(scope) != "write"
        )

        for scope in write_scopes:
            findings.append(
                self._finding(
                    change=change,
                    rule_id="permissions.write_scope_added",
                    severity="high",
                    title=f"Write permission added: {scope}",
                    description=(
                        f"The permission scope '{scope}' is now set to write. "
                        "This expands the workflow's declared write access."
                    ),
                    recommendation=(
                        f"Verify that '{scope}: write' is necessary; use read "
                        "access or omit the scope when write access is not required."
                    ),
                    confidence="high",
                )
            )

        return findings

    @staticmethod
    def _permission_mapping(value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None

        if not all(
            isinstance(scope, str)
            and isinstance(permission, str)
            and permission in {"read", "write", "none"}
            for scope, permission in value.items()
        ):
            return None

        return value

    @staticmethod
    def _finding(
        *,
        change: WorkflowChange,
        rule_id: str,
        severity: Severity,
        title: str,
        description: str,
        recommendation: str,
        confidence: Confidence,
    ) -> Finding:
        return Finding(
            rule_id=rule_id,
            severity=severity,
            title=title,
            description=description,
            evidence=(change,),
            location=change.source_location,
            recommendation=recommendation,
            confidence=confidence,
        )

    @staticmethod
    def _deduplicate(findings: list[Finding]) -> list[Finding]:
        unique: dict[tuple[Any, ...], Finding] = {}

        for finding in findings:
            key = (
                finding.rule_id,
                finding.severity,
                finding.title,
                finding.description,
                tuple(
                    (
                        item.category,
                        item.change_type,
                        item.path,
                        repr(item.before),
                        repr(item.after),
                        item.message,
                        (
                            item.source_location.line,
                            item.source_location.column,
                        )
                        if item.source_location is not None
                        else None,
                    )
                    for item in finding.evidence
                ),
            )
            unique.setdefault(key, finding)

        return list(unique.values())
