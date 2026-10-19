from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, SequenceNode


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    severity: str = "error"


@dataclass(frozen=True)
class SourceLocation:
    line: int
    column: int


@dataclass
class ParsedWorkflow:
    data: dict[str, Any] = field(default_factory=dict)
    locations: dict[str, SourceLocation] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    valid: bool = False


class WorkflowParser:
    """Parse workflow YAML without executing its expressions or commands."""

    def parse(self, source: str) -> ParsedWorkflow:
        result = ParsedWorkflow()

        if not isinstance(source, str):
            result.diagnostics.append(
                Diagnostic(
                    code="invalid_input",
                    message="Workflow source must be a string.",
                )
            )
            return result

        yaml = YAML(typ="safe")
        yaml.version = (1, 2)
        yaml.allow_duplicate_keys = False

        try:
            data = yaml.load(source)
        except YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            result.diagnostics.append(
                Diagnostic(
                    code="yaml_parse_error",
                    message="Workflow contains invalid or unsupported YAML syntax.",
                    line=mark.line + 1 if mark else None,
                    column=mark.column + 1 if mark else None,
                )
            )
            return result

        if data is None:
            result.diagnostics.append(
                Diagnostic(
                    code="empty_workflow",
                    message="Workflow document is empty.",
                )
            )
            return result

        if not isinstance(data, dict):
            result.diagnostics.append(
                Diagnostic(
                    code="invalid_root",
                    message="Workflow root must be a YAML mapping.",
                )
            )
            return result

        result.data = data
        result.locations = self._collect_locations(source)
        self._validate_structure(result)
        result.valid = not any(
            diagnostic.severity == "error" for diagnostic in result.diagnostics
        )
        return result

    @staticmethod
    def _collect_locations(source: str) -> dict[str, SourceLocation]:
        yaml = YAML(typ="safe")
        yaml.version = (1, 2)
        node = yaml.compose(source)
        locations: dict[str, SourceLocation] = {}

        def visit(current: Node, path: str) -> None:
            locations[path] = SourceLocation(
                line=current.start_mark.line + 1,
                column=current.start_mark.column + 1,
            )

            if isinstance(current, MappingNode):
                for key_node, value_node in current.value:
                    if not isinstance(key_node, Node):
                        continue

                    key = str(getattr(key_node, "value", ""))
                    child_path = f"{path}.{key}" if path else key
                    visit(key_node, child_path)
                    visit(value_node, child_path)

            elif isinstance(current, SequenceNode):
                for index, child in enumerate(current.value):
                    visit(child, f"{path}[{index}]")

        if node is not None:
            visit(node, "")

        return locations

    @staticmethod
    def _validate_structure(result: ParsedWorkflow) -> None:
        data = result.data

        if "on" not in data:
            result.diagnostics.append(
                Diagnostic(
                    code="missing_triggers",
                    message="Workflow must define the 'on' trigger key.",
                )
            )

        jobs = data.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            result.diagnostics.append(
                Diagnostic(
                    code="invalid_jobs",
                    message="Workflow must define a non-empty 'jobs' mapping.",
                    line=(
                        result.locations["jobs"].line
                        if "jobs" in result.locations
                        else None
                    ),
                )
            )
            return

        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                result.diagnostics.append(
                    Diagnostic(
                        code="invalid_job",
                        message=f"Job '{job_id}' must be a mapping.",
                        line=(
                            result.locations[f"jobs.{job_id}"].line
                            if f"jobs.{job_id}" in result.locations
                            else None
                        ),
                    )
                )
                continue

            needs = job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]

            if not isinstance(needs, list) or not all(
                isinstance(item, str) for item in needs
            ):
                result.diagnostics.append(
                    Diagnostic(
                        code="invalid_needs",
                        message=f"Job '{job_id}' has an invalid 'needs' value.",
                        line=(
                            result.locations[f"jobs.{job_id}.needs"].line
                            if f"jobs.{job_id}.needs" in result.locations
                            else None
                        ),
                    )
                )
                continue

            for dependency in needs:
                if dependency not in jobs:
                    result.diagnostics.append(
                        Diagnostic(
                            code="unknown_dependency",
                            message=(
                                f"Job '{job_id}' depends on unknown job '{dependency}'."
                            ),
                            line=(
                                result.locations[f"jobs.{job_id}.needs"].line
                                if f"jobs.{job_id}.needs" in result.locations
                                else None
                            ),
                        )
                    )
