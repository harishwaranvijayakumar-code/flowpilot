from dataclasses import dataclass
from typing import Any

from workflow_parser.normalizer import (
    NormalizedJob,
    NormalizedStep,
    NormalizedWorkflow,
)
from workflow_parser.parser import Diagnostic, SourceLocation


@dataclass(frozen=True)
class WorkflowChange:
    """One detected change between two workflow versions."""

    category: str
    change_type: str
    path: str
    before: Any = None
    after: Any = None
    source_location: SourceLocation | None = None
    message: str = ""


@dataclass(frozen=True)
class ComparisonResult:
    """Deterministic comparison output."""

    valid: bool
    changes: tuple[WorkflowChange, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class WorkflowComparator:
    """Compare normalized workflows without executing workflow content."""

    def compare(
        self,
        before: NormalizedWorkflow,
        after: NormalizedWorkflow,
    ) -> ComparisonResult:
        if not before.valid or not after.valid:
            diagnostics = self._unique_diagnostics(
                (*before.diagnostics, *after.diagnostics)
            )
            if not diagnostics:
                diagnostics = (
                    Diagnostic(
                        code="invalid_comparison_input",
                        message="Both workflows must be valid before comparison.",
                    ),
                )
            return ComparisonResult(valid=False, diagnostics=diagnostics)

        changes: list[WorkflowChange] = []

        self._compare_triggers(before, after, changes)
        self._compare_workflow_permissions(before, after, changes)
        self._compare_jobs(before, after, changes)

        ordered = tuple(
            sorted(
                changes,
                key=lambda change: (
                    change.path,
                    change.change_type,
                    change.message,
                ),
            )
        )

        return ComparisonResult(valid=True, changes=ordered)

    def _compare_triggers(
        self,
        before: NormalizedWorkflow,
        after: NormalizedWorkflow,
        changes: list[WorkflowChange],
    ) -> None:
        old = {trigger.name: trigger for trigger in before.triggers}
        new = {trigger.name: trigger for trigger in after.triggers}

        for name in sorted(old.keys() - new.keys()):
            trigger = old[name]
            self._add_change(
                changes,
                category="trigger",
                change_type="removed",
                path=f"on.{name}",
                before=self._plain(trigger.filters),
                after=None,
                location=trigger.source_location,
                message=f"Trigger '{name}' was removed.",
            )

        for name in sorted(new.keys() - old.keys()):
            trigger = new[name]
            self._add_change(
                changes,
                category="trigger",
                change_type="added",
                path=f"on.{name}",
                before=None,
                after=self._plain(trigger.filters),
                location=trigger.source_location,
                message=f"Trigger '{name}' was added.",
            )

        for name in sorted(old.keys() & new.keys()):
            old_filters = dict(old[name].filters)
            new_filters = dict(new[name].filters)
            self._compare_mapping_fields(
                category="trigger_filter",
                prefix=f"on.{name}",
                before=old_filters,
                after=new_filters,
                location=new[name].source_location or old[name].source_location,
                changes=changes,
            )

    def _compare_workflow_permissions(
        self,
        before: NormalizedWorkflow,
        after: NormalizedWorkflow,
        changes: list[WorkflowChange],
    ) -> None:
        self._compare_value(
            changes,
            category="permission",
            path="permissions",
            before=before.permissions,
            after=after.permissions,
            location=self._location(after, "permissions")
            or self._location(before, "permissions"),
        )

    def _compare_jobs(
        self,
        before: NormalizedWorkflow,
        after: NormalizedWorkflow,
        changes: list[WorkflowChange],
    ) -> None:
        old = {job.job_id: job for job in before.jobs}
        new = {job.job_id: job for job in after.jobs}

        for job_id in sorted(old.keys() - new.keys()):
            job = old[job_id]
            self._add_change(
                changes,
                category="job",
                change_type="removed",
                path=f"jobs.{job_id}",
                before=self._job_value(job),
                after=None,
                location=job.source_location,
                message=f"Job '{job_id}' was removed.",
            )

        for job_id in sorted(new.keys() - old.keys()):
            job = new[job_id]
            self._add_change(
                changes,
                category="job",
                change_type="added",
                path=f"jobs.{job_id}",
                before=None,
                after=self._job_value(job),
                location=job.source_location,
                message=f"Job '{job_id}' was added.",
            )

        for job_id in sorted(old.keys() & new.keys()):
            old_job = old[job_id]
            new_job = new[job_id]
            prefix = f"jobs.{job_id}"

            fields = (
                ("name", old_job.name, new_job.name),
                ("runs-on", old_job.runs_on, new_job.runs_on),
                ("needs", old_job.needs, new_job.needs),
                ("if", old_job.condition, new_job.condition),
                ("permissions", old_job.permissions, new_job.permissions),
                ("strategy", old_job.strategy, new_job.strategy),
                ("environment", old_job.environment, new_job.environment),
            )

            for field_name, old_value, new_value in fields:
                self._compare_value(
                    changes,
                    category=self._category_for_field(field_name),
                    path=f"{prefix}.{field_name}",
                    before=old_value,
                    after=new_value,
                    location=new_job.source_location or old_job.source_location,
                )

            self._compare_steps(old_job, new_job, changes)

    def _compare_steps(
        self,
        before: NormalizedJob,
        after: NormalizedJob,
        changes: list[WorkflowChange],
    ) -> None:
        """Compare steps using ordered identity-aware alignment."""
        aligned = self._align_steps(before.steps, after.steps)

        for old_index, new_index in aligned:
            if old_index is None:
                new_step = after.steps[new_index]
                self._add_change(
                    changes,
                    category="step",
                    change_type="added",
                    path=f"jobs.{after.job_id}.steps[{new_index}]",
                    before=None,
                    after=self._step_value(new_step),
                    location=new_step.source_location,
                    message=(f"Step {new_index} was added to job '{after.job_id}'."),
                )
                continue

            if new_index is None:
                old_step = before.steps[old_index]
                self._add_change(
                    changes,
                    category="step",
                    change_type="removed",
                    path=f"jobs.{before.job_id}.steps[{old_index}]",
                    before=self._step_value(old_step),
                    after=None,
                    location=old_step.source_location,
                    message=(
                        f"Step {old_index} was removed from job '{before.job_id}'."
                    ),
                )
                continue

            old_step = before.steps[old_index]
            new_step = after.steps[new_index]
            path = f"jobs.{after.job_id}.steps[{new_index}]"
            location = new_step.source_location or old_step.source_location

            fields = (
                ("name", old_step.name, new_step.name),
                ("id", old_step.step_id, new_step.step_id),
                ("uses", old_step.uses, new_step.uses),
                ("run", old_step.run, new_step.run),
                ("if", old_step.condition, new_step.condition),
                ("with", old_step.with_args, new_step.with_args),
                ("env", old_step.env, new_step.env),
            )

            for field_name, old_value, new_value in fields:
                self._compare_value(
                    changes,
                    category="step",
                    path=f"{path}.{field_name}",
                    before=old_value,
                    after=new_value,
                    location=location,
                )

    @classmethod
    def _align_steps(
        cls,
        before: tuple[NormalizedStep, ...],
        after: tuple[NormalizedStep, ...],
    ) -> list[tuple[int | None, int | None]]:
        """
        Align steps in order, preferring stable identities over list positions.

        The result contains (old_index, new_index) pairs. A None index
        represents an insertion or removal.
        """
        old_count = len(before)
        new_count = len(after)
        gap_penalty = -1

        scores = [[0] * (new_count + 1) for _ in range(old_count + 1)]
        decisions: list[list[str | None]] = [
            [None] * (new_count + 1) for _ in range(old_count + 1)
        ]

        for old_index in range(1, old_count + 1):
            scores[old_index][0] = old_index * gap_penalty
            decisions[old_index][0] = "remove"

        for new_index in range(1, new_count + 1):
            scores[0][new_index] = new_index * gap_penalty
            decisions[0][new_index] = "add"

        for old_index in range(1, old_count + 1):
            for new_index in range(1, new_count + 1):
                old_step = before[old_index - 1]
                new_step = after[new_index - 1]
                identity_score = cls._step_identity_score(old_step, new_step)

                candidates: list[tuple[int, str, int]] = [
                    (
                        scores[old_index - 1][new_index] + gap_penalty,
                        "remove",
                        1,
                    ),
                    (
                        scores[old_index][new_index - 1] + gap_penalty,
                        "add",
                        0,
                    ),
                ]

                if identity_score is not None:
                    candidates.append(
                        (
                            scores[old_index - 1][new_index - 1] + identity_score,
                            "match",
                            2,
                        )
                    )

                # Stable tie-breaking makes alignment deterministic.
                best_score, best_decision, _ = max(
                    candidates,
                    key=lambda candidate: (candidate[0], candidate[2]),
                )
                scores[old_index][new_index] = best_score
                decisions[old_index][new_index] = best_decision

        aligned: list[tuple[int | None, int | None]] = []
        old_index = old_count
        new_index = new_count

        while old_index > 0 or new_index > 0:
            decision = decisions[old_index][new_index]

            if decision == "match":
                aligned.append((old_index - 1, new_index - 1))
                old_index -= 1
                new_index -= 1
            elif decision == "remove":
                aligned.append((old_index - 1, None))
                old_index -= 1
            elif decision == "add":
                aligned.append((None, new_index - 1))
                new_index -= 1
            else:
                raise RuntimeError("Step alignment could not be reconstructed.")

        aligned.reverse()
        return aligned

    @classmethod
    def _step_identity_score(
        cls,
        before: NormalizedStep,
        after: NormalizedStep,
    ) -> int | None:
        """Return a positive identity score, or None if identity is uncertain."""
        if before.step_id and after.step_id:
            if before.step_id != after.step_id:
                return None
            return 100

        if before.name and after.name and before.name == after.name:
            return 60

        if before.uses and after.uses and before.uses == after.uses:
            return 50

        if before.run and after.run and before.run == after.run:
            return 40

        if cls._step_value(before) == cls._step_value(after):
            return 35

        # Without a stable identifier, matching changed content by position
        # would risk hiding a real removal and addition.
        return None

    def _compare_mapping_fields(
        self,
        *,
        category: str,
        prefix: str,
        before: dict[str, Any],
        after: dict[str, Any],
        location: SourceLocation | None,
        changes: list[WorkflowChange],
    ) -> None:
        for key in sorted(before.keys() | after.keys()):
            old_value = before.get(key)
            new_value = after.get(key)

            self._compare_value(
                changes,
                category=category,
                path=f"{prefix}.{key}",
                before=old_value,
                after=new_value,
                location=location,
            )

    def _compare_value(
        self,
        changes: list[WorkflowChange],
        *,
        category: str,
        path: str,
        before: Any,
        after: Any,
        location: SourceLocation | None,
    ) -> None:
        if before == after:
            return

        if before is None:
            change_type = "added"
            message = f"'{path}' was added."
        elif after is None:
            change_type = "removed"
            message = f"'{path}' was removed."
        else:
            change_type = "modified"
            message = f"'{path}' changed."

        self._add_change(
            changes,
            category=category,
            change_type=change_type,
            path=path,
            before=self._plain(before),
            after=self._plain(after),
            location=location,
            message=message,
        )

    @staticmethod
    def _add_change(
        changes: list[WorkflowChange],
        *,
        category: str,
        change_type: str,
        path: str,
        before: Any,
        after: Any,
        location: SourceLocation | None,
        message: str,
    ) -> None:
        changes.append(
            WorkflowChange(
                category=category,
                change_type=change_type,
                path=path,
                before=before,
                after=after,
                source_location=location,
                message=message,
            )
        )

    @staticmethod
    def _category_for_field(field_name: str) -> str:
        if field_name == "permissions":
            return "permission"
        if field_name == "needs":
            return "dependency"
        if field_name == "if":
            return "condition"
        return "job"

    @classmethod
    def _job_value(cls, job: NormalizedJob) -> dict[str, Any]:
        return {
            "name": job.name,
            "runs_on": cls._plain(job.runs_on),
            "needs": list(job.needs),
            "condition": job.condition,
            "permissions": cls._plain(job.permissions),
            "strategy": cls._plain(job.strategy),
            "environment": cls._plain(job.environment),
            "steps": [cls._step_value(step) for step in job.steps],
        }

    @classmethod
    def _step_value(cls, step: NormalizedStep) -> dict[str, Any]:
        return {
            "name": step.name,
            "id": step.step_id,
            "uses": step.uses,
            "run": step.run,
            "condition": step.condition,
            "with": cls._plain(step.with_args),
            "env": cls._plain(step.env),
        }

    @classmethod
    def _plain(cls, value: Any) -> Any:
        """Convert frozen normalized values to ordinary serializable structures."""
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

    @staticmethod
    def _location(
        workflow: NormalizedWorkflow,
        path: str,
    ) -> SourceLocation | None:
        locations = dict(workflow.source_locations)
        return locations.get(path)

    @staticmethod
    def _unique_diagnostics(
        diagnostics: tuple[Diagnostic, ...],
    ) -> tuple[Diagnostic, ...]:
        unique: list[Diagnostic] = []
        seen: set[tuple[str, str, int | None, int | None, str]] = set()

        for diagnostic in diagnostics:
            key = (
                diagnostic.code,
                diagnostic.message,
                diagnostic.line,
                diagnostic.column,
                diagnostic.severity,
            )
            if key not in seen:
                seen.add(key)
                unique.append(diagnostic)

        return tuple(unique)
