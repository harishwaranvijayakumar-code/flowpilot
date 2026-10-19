from dataclasses import dataclass
from typing import Any

from workflow_parser.parser import Diagnostic, ParsedWorkflow, SourceLocation


@dataclass(frozen=True)
class NormalizedTrigger:
    """One workflow event and its normalized event filters."""

    name: str
    filters: tuple[tuple[str, Any], ...] = ()
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class NormalizedStep:
    """The statically relevant fields of a workflow step."""

    name: str | None = None
    step_id: str | None = None
    uses: str | None = None
    run: str | None = None
    condition: str | None = None
    with_args: tuple[tuple[str, Any], ...] = ()
    env: tuple[tuple[str, Any], ...] = ()
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class NormalizedJob:
    """The statically relevant fields of a workflow job."""

    job_id: str
    name: str | None = None
    runs_on: Any = None
    needs: tuple[str, ...] = ()
    condition: str | None = None
    permissions: Any = None
    strategy: Any = None
    environment: Any = None
    steps: tuple[NormalizedStep, ...] = ()
    source_location: SourceLocation | None = None


@dataclass(frozen=True)
class NormalizedWorkflow:
    """A stable, source-aware representation for workflow comparison."""

    name: str | None = None
    triggers: tuple[NormalizedTrigger, ...] = ()
    permissions: Any = None
    jobs: tuple[NormalizedJob, ...] = ()
    source_locations: tuple[tuple[str, SourceLocation], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    valid: bool = False


class WorkflowNormalizer:
    """Convert parsed workflow YAML into a deterministic data model."""

    def normalize(self, parsed: ParsedWorkflow) -> NormalizedWorkflow:
        diagnostics = list(parsed.diagnostics)

        if not isinstance(parsed.data, dict):
            diagnostics = self._deduplicate_diagnostics(diagnostics)
            return NormalizedWorkflow(
                diagnostics=tuple(diagnostics),
                valid=False,
            )

        data = parsed.data
        locations = parsed.locations

        triggers = self._normalize_triggers(
            data.get("on"),
            locations,
            diagnostics,
        )

        permissions = data.get("permissions")
        if permissions is not None and not self._valid_permissions(permissions):
            diagnostics.append(
                Diagnostic(
                    code="invalid_permissions",
                    message=(
                        "Workflow-level permissions must be a mapping "
                        "or 'read-all'/'write-all'."
                    ),
                    line=self._line(locations, "permissions"),
                )
            )
            permissions = None

        jobs_data = data.get("jobs")
        jobs: list[NormalizedJob] = []

        if isinstance(jobs_data, dict):
            for job_id, job_data in sorted(
                jobs_data.items(),
                key=lambda item: str(item[0]),
            ):
                if not isinstance(job_id, str) or not isinstance(job_data, dict):
                    continue

                jobs.append(
                    self._normalize_job(
                        job_id,
                        job_data,
                        locations,
                        diagnostics,
                    )
                )

        known_job_ids = {job.job_id for job in jobs}

        for job in jobs:
            for dependency in job.needs:
                if dependency not in known_job_ids:
                    diagnostics.append(
                        Diagnostic(
                            code="unknown_dependency",
                            message=(
                                f"Job '{job.job_id}' depends on unknown job "
                                f"'{dependency}'."
                            ),
                            line=self._line(
                                locations,
                                f"jobs.{job.job_id}.needs",
                            ),
                        )
                    )

        diagnostics = self._deduplicate_diagnostics(diagnostics)
        has_errors = any(item.severity == "error" for item in diagnostics)

        return NormalizedWorkflow(
            name=data.get("name") if isinstance(data.get("name"), str) else None,
            triggers=tuple(triggers),
            permissions=self._freeze(permissions),
            jobs=tuple(jobs),
            source_locations=tuple(sorted(locations.items())),
            diagnostics=tuple(diagnostics),
            valid=parsed.valid and not has_errors,
        )

    def _normalize_triggers(
        self,
        value: Any,
        locations: dict[str, SourceLocation],
        diagnostics: list[Diagnostic],
    ) -> list[NormalizedTrigger]:
        if isinstance(value, str):
            event_names = [(value, None)]
        elif isinstance(value, list):
            event_names = []

            for event in value:
                if isinstance(event, str):
                    event_names.append((event, None))
                else:
                    diagnostics.append(
                        Diagnostic(
                            code="invalid_trigger",
                            message=(
                                "Every workflow trigger in a list must be a string."
                            ),
                            line=self._line(locations, "on"),
                        )
                    )
        elif isinstance(value, dict):
            event_names = list(value.items())
        else:
            diagnostics.append(
                Diagnostic(
                    code="invalid_triggers",
                    message=(
                        "Workflow triggers must be a string, a list of strings, "
                        "or a mapping."
                    ),
                    line=self._line(locations, "on"),
                )
            )
            return []

        triggers: list[NormalizedTrigger] = []

        for event_name, config in event_names:
            if not isinstance(event_name, str):
                diagnostics.append(
                    Diagnostic(
                        code="invalid_trigger",
                        message="Workflow trigger names must be strings.",
                        line=self._line(locations, "on"),
                    )
                )
                continue

            if config is None:
                filters: tuple[tuple[str, Any], ...] = ()
            elif isinstance(config, dict):
                if not all(isinstance(key, str) for key in config):
                    diagnostics.append(
                        Diagnostic(
                            code="invalid_trigger_filters",
                            message=(
                                f"Trigger '{event_name}' filter names must be strings."
                            ),
                            line=self._line(locations, f"on.{event_name}"),
                        )
                    )
                    continue

                filters = self._freeze_mapping(config)
            else:
                diagnostics.append(
                    Diagnostic(
                        code="invalid_trigger_config",
                        message=(
                            f"Trigger '{event_name}' must have a mapping "
                            "of event filters or no configuration."
                        ),
                        line=self._line(locations, f"on.{event_name}"),
                    )
                )
                continue

            triggers.append(
                NormalizedTrigger(
                    name=event_name,
                    filters=filters,
                    source_location=(
                        locations.get(f"on.{event_name}") or locations.get("on")
                    ),
                )
            )

        return sorted(triggers, key=lambda trigger: trigger.name)

    def _normalize_job(
        self,
        job_id: str,
        data: dict[str, Any],
        locations: dict[str, SourceLocation],
        diagnostics: list[Diagnostic],
    ) -> NormalizedJob:
        job_path = f"jobs.{job_id}"

        permissions = data.get("permissions")
        if permissions is not None and not self._valid_permissions(permissions):
            diagnostics.append(
                Diagnostic(
                    code="invalid_permissions",
                    message=(
                        f"Job '{job_id}' permissions must be a mapping "
                        "or 'read-all'/'write-all'."
                    ),
                    line=self._line(locations, f"{job_path}.permissions"),
                )
            )
            permissions = None

        needs = data.get("needs", [])

        if isinstance(needs, str):
            needs = [needs]

        if not isinstance(needs, list) or not all(
            isinstance(item, str) for item in needs
        ):
            diagnostics.append(
                Diagnostic(
                    code="invalid_needs",
                    message=f"Job '{job_id}' has an invalid 'needs' value.",
                    line=self._line(locations, f"{job_path}.needs"),
                )
            )
            needs = []

        steps_data = data.get("steps", [])
        steps: list[NormalizedStep] = []

        if not isinstance(steps_data, list):
            diagnostics.append(
                Diagnostic(
                    code="invalid_steps",
                    message=f"Job '{job_id}' steps must be a list.",
                    line=self._line(locations, f"{job_path}.steps"),
                )
            )
        else:
            for index, step_data in enumerate(steps_data):
                step_path = f"{job_path}.steps[{index}]"

                if not isinstance(step_data, dict):
                    diagnostics.append(
                        Diagnostic(
                            code="invalid_step",
                            message=(
                                f"Step {index} in job '{job_id}' must be a mapping."
                            ),
                            line=self._line(locations, step_path),
                        )
                    )
                    continue

                with_args = self._normalize_string_mapping(
                    step_data.get("with", {}),
                    field_name="with",
                    job_id=job_id,
                    step_index=index,
                    path=step_path,
                    locations=locations,
                    diagnostics=diagnostics,
                )

                env = self._normalize_string_mapping(
                    step_data.get("env", {}),
                    field_name="env",
                    job_id=job_id,
                    step_index=index,
                    path=step_path,
                    locations=locations,
                    diagnostics=diagnostics,
                )

                steps.append(
                    NormalizedStep(
                        name=self._optional_string(step_data.get("name")),
                        step_id=self._optional_string(step_data.get("id")),
                        uses=self._optional_string(step_data.get("uses")),
                        run=self._optional_string(step_data.get("run")),
                        condition=self._optional_string(step_data.get("if")),
                        with_args=with_args,
                        env=env,
                        source_location=locations.get(step_path),
                    )
                )

        return NormalizedJob(
            job_id=job_id,
            name=self._optional_string(data.get("name")),
            runs_on=self._freeze(data.get("runs-on")),
            needs=tuple(sorted(set(needs))),
            condition=self._optional_string(data.get("if")),
            permissions=self._freeze(permissions),
            strategy=self._freeze(data.get("strategy")),
            environment=self._freeze(data.get("environment")),
            steps=tuple(steps),
            source_location=locations.get(job_path),
        )

    def _normalize_string_mapping(
        self,
        value: Any,
        *,
        field_name: str,
        job_id: str,
        step_index: int,
        path: str,
        locations: dict[str, SourceLocation],
        diagnostics: list[Diagnostic],
    ) -> tuple[tuple[str, Any], ...]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            diagnostics.append(
                Diagnostic(
                    code=f"invalid_step_{field_name}",
                    message=(
                        f"Step {step_index} in job '{job_id}' must define "
                        f"'{field_name}' as a mapping with string keys."
                    ),
                    line=self._line(locations, f"{path}.{field_name}"),
                )
            )
            return ()

        return self._freeze_mapping(value)

    @staticmethod
    def _valid_permissions(value: Any) -> bool:
        if isinstance(value, str):
            return value in {"read-all", "write-all"}

        return isinstance(value, dict) and all(
            isinstance(scope, str)
            and isinstance(permission, str)
            and permission in {"read", "write", "none"}
            for scope, permission in value.items()
        )

    @classmethod
    def _freeze_mapping(cls, value: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
        return tuple(
            (key, cls._freeze(item))
            for key, item in sorted(value.items(), key=lambda pair: pair[0])
        )

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        """Recursively make YAML values deterministic and immutable."""
        if isinstance(value, dict):
            if all(isinstance(key, str) for key in value):
                return cls._freeze_mapping(value)

            return tuple(
                sorted(
                    (
                        (cls._freeze(key), cls._freeze(item))
                        for key, item in value.items()
                    ),
                    key=lambda pair: repr(pair[0]),
                )
            )

        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)

        if isinstance(value, tuple):
            return tuple(cls._freeze(item) for item in value)

        return value

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _line(
        locations: dict[str, SourceLocation],
        path: str,
    ) -> int | None:
        location = locations.get(path)
        return location.line if location else None

    @staticmethod
    def _deduplicate_diagnostics(
        diagnostics: list[Diagnostic],
    ) -> list[Diagnostic]:
        """Remove identical diagnostics while preserving their original order."""
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

        return unique
