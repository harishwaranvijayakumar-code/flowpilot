import pytest

from workflow_parser.analysis import WorkflowAnalyzer
from workflow_parser.comparison import ComparisonResult, WorkflowChange


def analyze_permission_change(
    before,
    after,
    *,
    path="permissions",
    change_type="modified",
):
    change = WorkflowChange(
        category="permission",
        change_type=change_type,
        path=path,
        before=before,
        after=after,
        message=f"'{path}' changed.",
    )

    return WorkflowAnalyzer().analyze(ComparisonResult(valid=True, changes=(change,)))


@pytest.mark.parametrize(
    ("before", "after", "path"),
    [
        (
            {"contents": "read"},
            {"contents": "write"},
            "permissions",
        ),
        (
            {"contents": "read"},
            {"contents": "read", "issues": "write"},
            "permissions",
        ),
        (
            {"contents": "none"},
            {"contents": "write"},
            "permissions",
        ),
        (
            {"contents": "read"},
            {"contents": "write"},
            "jobs.build.permissions",
        ),
    ],
)
def test_explicit_write_access_increases_are_reported(
    before,
    after,
    path,
):
    result = analyze_permission_change(before, after, path=path)

    findings = [
        finding
        for finding in result.findings
        if finding.rule_id == "permissions.write_scope_added"
    ]

    assert result.valid
    assert findings
    assert findings[0].severity == "high"
    assert findings[0].evidence[0].path == path


def test_write_all_transition_is_reported():
    result = analyze_permission_change(
        {"contents": "read"},
        "write-all",
    )

    assert [finding.rule_id for finding in result.findings] == [
        "permissions.write_all_enabled"
    ]


def test_write_all_removal_is_not_reported_as_an_increase():
    result = analyze_permission_change(
        "write-all",
        {"contents": "read"},
    )

    assert not result.findings


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, {"contents": "write"}),
        ("read-all", {"contents": "write"}),
        ({"contents": "read"}, None),
        ({"contents": "read"}, "read-all"),
    ],
)
def test_ambiguous_permission_transitions_are_not_inferred(
    before,
    after,
):
    result = analyze_permission_change(before, after)

    assert result.valid
    assert not result.findings


def test_read_only_change_is_not_reported_as_write_access():
    result = analyze_permission_change(
        {"contents": "none"},
        {"contents": "read"},
    )

    assert not result.findings


def test_unchanged_write_scope_is_not_reported():
    result = analyze_permission_change(
        {"contents": "write"},
        {"contents": "write"},
    )

    assert not result.findings


def test_write_scope_findings_keep_the_original_change_as_evidence():
    before = {"contents": "read"}
    after = {"contents": "write"}

    result = analyze_permission_change(before, after)

    finding = next(
        finding
        for finding in result.findings
        if finding.rule_id == "permissions.write_scope_added"
    )

    evidence = finding.evidence[0]
    assert evidence.before == before
    assert evidence.after == after
    assert evidence.path == "permissions"


def test_permission_analysis_is_deterministic():
    before = {"contents": "read"}
    after = {
        "contents": "write",
        "issues": "write",
    }

    first = analyze_permission_change(before, after).to_dict()
    second = analyze_permission_change(before, after).to_dict()

    assert first == second
