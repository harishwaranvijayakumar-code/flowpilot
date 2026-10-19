from dataclasses import dataclass
from typing import Any

from workflow_parser.analysis import WorkflowAnalyzer
from workflow_parser.comparison import WorkflowComparator
from workflow_parser.normalizer import WorkflowNormalizer
from workflow_parser.parser import WorkflowParser


@dataclass(frozen=True)
class WorkflowAnalysisOutput:
    """Serializable output produced by workflow analysis."""

    valid: bool
    findings: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    schema_version: int = 1


def analyze_workflow_payload(payload: dict[str, Any]) -> WorkflowAnalysisOutput:
    """Analyze the before and after workflow YAML in a queued job."""
    before_yaml = payload.get("before_yaml")
    after_yaml = payload.get("after_yaml")

    if not isinstance(before_yaml, str) or not isinstance(after_yaml, str):
        raise ValueError(
            "workflow_analysis payload requires string fields "
            "'before_yaml' and 'after_yaml'."
        )

    parser = WorkflowParser()
    normalizer = WorkflowNormalizer()
    comparator = WorkflowComparator()
    analyzer = WorkflowAnalyzer()

    before_parsed = parser.parse(before_yaml)
    after_parsed = parser.parse(after_yaml)

    before = normalizer.normalize(before_parsed)
    after = normalizer.normalize(after_parsed)

    comparison = comparator.compare(before, after)
    result = analyzer.analyze(comparison)
    serialized = result.to_dict()

    return WorkflowAnalysisOutput(
        valid=serialized["valid"],
        findings=serialized["findings"],
        diagnostics=serialized["diagnostics"],
    )
