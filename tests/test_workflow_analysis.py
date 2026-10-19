import json

import pytest

from workflow_parser.analysis import WorkflowAnalyzer
from workflow_parser.comparison import ComparisonResult, WorkflowChange
from workflow_parser.parser import Diagnostic, SourceLocation


@pytest.fixture
def analyzer():
    return WorkflowAnalyzer()


def change(
    *,
    category,
    change_type="modified",
    path,
    before=None,
    after=None,
    line=12,
    column=5,
):
    return WorkflowChange(
        category=category,
        change_type=change_type,
        path=path,
        before=before,
        after=after,
        source_location=(
            SourceLocation(line=line, column=column)
            if line is not None and column is not None
            else None
        ),
        message=f"Changed {path}",
    )


def comparison(*changes):
    return ComparisonResult(valid=True, changes=tuple(changes))


def test_write_permission_increase_creates_finding(analyzer):
    item = change(
        category="permission",
        path="permissions",
        before={"contents": "read"},
        after={"contents": "write"},
    )

    result = analyzer.analyze(comparison(item))

    assert result.valid
    assert len(result.findings) == 1

    finding = result.findings[0]
    assert finding.rule_id == "permissions.write_scope_added"
    assert finding.severity == "high"
    assert finding.evidence == (item,)
    assert finding.location == SourceLocation(line=12, column=5)


def test_write_all_creates_finding(analyzer):
    item = change(
        category="permission",
        path="permissions",
        before={"contents": "read"},
        after="write-all",
    )

    result = analyzer.analyze(comparison(item))

    assert [finding.rule_id for finding in result.findings] == [
        "permissions.write_all_enabled"
    ]


def test_pull_request_target_addition_creates_finding(analyzer):
    item = change(
        category="trigger",
        change_type="added",
        path="on.pull_request_target",
        after={},
    )

    result = analyzer.analyze(comparison(item))

    assert [finding.rule_id for finding in result.findings] == [
        "triggers.pull_request_target_added"
    ]


def test_changed_action_reference_creates_finding(analyzer):
    item = change(
        category="step",
        path="jobs.test.steps[0].uses",
        before="actions/checkout@v4",
        after="actions/checkout@v5",
    )

    result = analyzer.analyze(comparison(item))

    assert [finding.rule_id for finding in result.findings] == [
        "actions.reference_changed"
    ]


def test_read_permission_change_does_not_create_write_finding(analyzer):
    item = change(
        category="permission",
        path="permissions",
        before={"contents": "none"},
        after={"contents": "read"},
    )

    result = analyzer.analyze(comparison(item))

    assert result.findings == ()


@pytest.mark.parametrize(
    "before,after",
    [
        (None, {"contents": "write"}),
        ("read-all", {"contents": "write"}),
        ({"contents": "read"}, None),
    ],
)
def test_ambiguous_permission_forms_are_not_inferred(
    analyzer,
    before,
    after,
):
    item = change(
        category="permission",
        path="permissions",
        before=before,
        after=after,
    )

    result = analyzer.analyze(comparison(item))

    assert result.findings == ()


def test_unrelated_change_does_not_create_finding(analyzer):
    item = change(
        category="job",
        path="jobs.test.name",
        before="Tests",
        after="Unit tests",
    )

    result = analyzer.analyze(comparison(item))

    assert result.findings == ()


def test_invalid_comparison_returns_diagnostics(analyzer):
    result = analyzer.analyze(
        ComparisonResult(
            valid=False,
            diagnostics=(
                Diagnostic(
                    code="invalid_workflow",
                    message="Workflow is invalid.",
                ),
            ),
        )
    )

    assert not result.valid
    assert result.findings == ()
    assert result.diagnostics[0].code == "invalid_workflow"


def test_invalid_comparison_without_diagnostics_gets_fallback(analyzer):
    result = analyzer.analyze(ComparisonResult(valid=False))

    assert not result.valid
    assert result.findings == ()
    assert result.diagnostics[0].code == "invalid_comparison"


def test_findings_are_deterministically_ordered(analyzer):
    first = change(
        category="step",
        path="jobs.test.steps[0].uses",
        before="owner/first@v1",
        after="owner/first@v2",
    )
    second = change(
        category="trigger",
        change_type="added",
        path="on.pull_request_target",
        after={},
    )

    result_a = analyzer.analyze(comparison(first, second))
    result_b = analyzer.analyze(comparison(second, first))

    assert result_a.findings == result_b.findings


def test_result_is_json_serializable(analyzer):
    item = change(
        category="step",
        path="jobs.test.steps[0].uses",
        before="actions/checkout@v4",
        after="actions/checkout@v5",
    )

    serialized = json.dumps(analyzer.analyze(comparison(item)).to_dict())

    assert "actions.reference_changed" in serialized
    assert '"line": 12' in serialized


def test_missing_source_location_is_supported(analyzer):
    item = change(
        category="trigger",
        change_type="added",
        path="on.pull_request_target",
        after={},
        line=None,
        column=None,
    )

    result = analyzer.analyze(comparison(item))

    assert len(result.findings) == 1
    assert result.findings[0].location is None
    assert result.findings[0].to_dict()["location"] is None


def test_confidence_is_separate_from_severity(analyzer):
    item = change(
        category="step",
        path="jobs.test.steps[0].uses",
        before="actions/checkout@v4",
        after="actions/checkout@v5",
    )

    finding = analyzer.analyze(comparison(item)).findings[0]

    assert finding.severity == "medium"
    assert finding.confidence == "high"


def test_added_action_reference_creates_finding(analyzer):
    item = change(
        category="step",
        change_type="added",
        path="jobs.test.steps[1]",
        after={
            "name": "Checkout",
            "uses": "actions/checkout@v4",
            "run": None,
        },
    )

    result = analyzer.analyze(comparison(item))

    assert [finding.rule_id for finding in result.findings] == [
        "actions.reference_added"
    ]
    assert result.findings[0].evidence == (item,)
    assert result.findings[0].location == item.source_location


def test_added_shell_step_does_not_create_action_reference_finding(analyzer):
    item = change(
        category="step",
        change_type="added",
        path="jobs.test.steps[1]",
        after={
            "name": "Test",
            "uses": None,
            "run": "pytest",
        },
    )

    result = analyzer.analyze(comparison(item))

    assert not any(
        finding.rule_id == "actions.reference_added" for finding in result.findings
    )


def test_distinct_source_locations_are_not_deduplicated(analyzer):
    first = change(
        category="step",
        path="jobs.test.steps[0].uses",
        before="actions/checkout@v4",
        after="actions/checkout@v5",
        line=10,
        column=9,
    )
    second = change(
        category="step",
        path="jobs.test.steps[0].uses",
        before="actions/checkout@v4",
        after="actions/checkout@v5",
        line=20,
        column=9,
    )

    result = analyzer.analyze(comparison(first, second))

    assert len(result.findings) == 2
    assert {
        finding.location.line
        for finding in result.findings
        if finding.location is not None
    } == {10, 20}
