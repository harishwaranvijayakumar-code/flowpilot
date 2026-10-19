from dataclasses import dataclass
from typing import Any

from workflow_parser.comparison import ComparisonResult, WorkflowChange
from workflow_parser.normalizer import NormalizedWorkflow
from workflow_parser.parser import SourceLocation


@dataclass(frozen=True)
class Finding:
    """Evidence-backed assessment of a workflow change."""

    rule_id: str
    severity: str
    confidence: str
    category: str
    path: str
    title: str
    summary: str
    evidence: tuple[str, ...]
    recommendation: str
    source_location: SourceLocation | None = None


class WorkflowAnalyzer:
    """Classify workflow changes without executing workflow content."""

    _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    _PERMISSION_RANK = {"none": 0, "read": 1, "write": 2}

    def analyze(
        self,
        before: NormalizedWorkflow,
        after: NormalizedWorkflow,
        comparison: ComparisonResult,
    ) -> tuple[Finding, ...]:
        if not before.valid or not after.valid or not comparison.valid:
            return ()

        findings = [
            *self._permission_findings(comparison.changes),
            *self._unpinned_action_findings(comparison.changes),
            *self._privileged_pull_request_findings(
                before, after, comparison.changes
            ),
        ]

        unique: dict[tuple[str, str], Finding] = {}
        for finding in findings:
            unique.setdefault((finding.rule_id, finding.path), finding)

        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    self._SEVERITY_ORDER[item.severity],
                    item.path,
                    item.rule_id,
                ),
            )
        )

    def _permission_findings(
        self, changes: tuple[WorkflowChange, ...]
    ) -> list[Finding]:
        findings = []
        for change in changes:
            if change.category != "permission":
                continue
            before = self._plain(change.before)
            after = self._plain(change.after)
            if not self._permission_broadening(before, after):
                continue

            findings.append(
                Finding(
                    rule_id="permissions-broadened",
                    severity="high",
                    confidence="high",
                    category="security",
                    path=change.path,
                    title="Workflow permissions were broadened",
                    summary=(
                        "The change grants broader GitHub token permissions than "
                        "the previous workflow configuration."
                    ),
                    evidence=(
                        f"Before: {before!r}",
                        f"After: {after!r}",
                        change.message,
                    ),
                    recommendation=(
                        "Grant only the permissions required. Prefer explicit "
                        "read-only scopes and elevate permissions only for the "
                        "job that needs them."
                    ),
                    source_location=change.source_location,
                )
            )
        return findings

    @classmethod
    def _permission_broadening(cls, before: Any, after: Any) -> bool:
        before = cls._plain(before)
        after = cls._plain(after)

        if before == after:
            return False
        if after == "write-all":
            return before != "write-all"
        if after == "read-all":
            return before is None or before == {} or before == "read-all"
        if not isinstance(after, dict):
            return False

        old_scopes = before if isinstance(before, dict) else {}
        for scope, level in after.items():
            old_level = old_scopes.get(scope, "none")
            if (
                level in cls._PERMISSION_RANK
                and old_level in cls._PERMISSION_RANK
                and cls._PERMISSION_RANK[level] > cls._PERMISSION_RANK[old_level]
            ):
                return True
        return False

    def _unpinned_action_findings(
        self, changes: tuple[WorkflowChange, ...]
    ) -> list[Finding]:
        findings = []
        for change in changes:
            if not change.path.endswith(".uses") or not isinstance(change.after, str):
                continue

            reference = change.after
            if not reference or reference.startswith("./") or "@" not in reference:
                continue

            revision = reference.rsplit("@", 1)[1]
            is_full_sha = len(revision) == 40 and all(
                character in "0123456789abcdefABCDEF" for character in revision
            )
            if is_full_sha:
                continue

            findings.append(
                Finding(
                    rule_id="action-reference-not-pinned",
                    severity="medium",
                    confidence="high",
                    category="supply-chain",
                    path=change.path,
                    title="Action reference is not pinned to a full commit SHA",
                    summary=(
                        "The changed action reference uses a tag or branch instead "
                        "of a full commit SHA, so the referenced code may change "
                        "without a workflow diff."
                    ),
                    evidence=(f"New action reference: {reference}", change.message),
                    recommendation=(
                        "Pin third-party actions to a verified full commit SHA and "
                        "review updates before changing the pinned revision."
                    ),
                    source_location=change.source_location,
                )
            )
        return findings

    def _privileged_pull_request_findings(
        self,
        before: NormalizedWorkflow,
        after: NormalizedWorkflow,
        changes: tuple[WorkflowChange, ...],
    ) -> list[Finding]:
        old_events = {trigger.name for trigger in before.triggers}
        new_events = {trigger.name for trigger in after.triggers}
        if "pull_request_target" not in new_events:
            return []

        checkout_steps = [
            (job.job_id, step)
            for job in after.jobs
            for step in job.steps
            if step.uses and step.uses.startswith("actions/checkout@")
        ]
        executable_steps = [
            (job.job_id, step)
            for job in after.jobs
            for step in job.steps
            if step.run and step.run.strip()
        ]
        if not checkout_steps or not executable_steps:
            return []

        trigger_added = "pull_request_target" not in old_events
        checkout_changed = any(
            change.path.endswith(".uses")
            and isinstance(change.after, str)
            and change.after.startswith("actions/checkout@")
            for change in changes
        )
        executable_changed = any(
            change.path.endswith(".run")
            and change.change_type in {"added", "modified"}
            for change in changes
        )
        if not (trigger_added or checkout_changed or executable_changed):
            return []

        evidence = [
            (
                "The workflow uses pull_request_target, which runs in the base "
                "repository context."
            ),
            "The workflow contains an actions/checkout step.",
            "The workflow contains a shell run step.",
        ]
        for job_id, step in checkout_steps:
            with_args = self._plain(step.with_args)
            ref = with_args.get("ref") if isinstance(with_args, dict) else None
            if isinstance(ref, str) and (
                "github.event.pull_request.head" in ref
                or "github.event.pull_request.head.sha" in ref
            ):
                evidence.append(
                    f"Job '{job_id}' checks out a pull-request head reference: {ref}"
                )

        trigger_change = next(
            (
                change
                for change in changes
                if change.path == "on.pull_request_target"
                and change.change_type == "added"
            ),
            None,
        )
        return [
            Finding(
                rule_id="privileged-pull-request-execution",
                severity="high",
                confidence="medium",
                category="security",
                path="on.pull_request_target",
                title="Privileged pull-request workflow needs a security review",
                summary=(
                    "Combining pull_request_target with checkout and shell execution "
                    "can be dangerous when untrusted pull-request content is run."
                ),
                evidence=tuple(evidence),
                recommendation=(
                    "Do not execute code from the pull-request head in a "
                    "pull_request_target workflow. Prefer pull_request for untrusted "
                    "builds, or isolate privileged operations from untrusted code "
                    "and review token permissions and secrets access."
                ),
                source_location=(
                    trigger_change.source_location if trigger_change else None
                ),
            )
        ]

    @classmethod
    def _plain(cls, value: Any) -> Any:
        if isinstance(value, tuple):
            if all(isinstance(item, tuple) and len(item) == 2 for item in value):
                return {str(key): cls._plain(item) for key, item in value}
            return [cls._plain(item) for item in value]
        if isinstance(value, list):
            return [cls._plain(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): cls._plain(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        return value
