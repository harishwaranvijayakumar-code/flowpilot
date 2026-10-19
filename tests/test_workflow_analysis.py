import pytest

from workflow_parser.analysis import WorkflowAnalyzer
from workflow_parser.comparison import WorkflowComparator
from workflow_parser.normalizer import WorkflowNormalizer
from workflow_parser.parser import WorkflowParser


@pytest.fixture
def pipeline():
    parser = WorkflowParser()
    normalizer = WorkflowNormalizer()
    return parser, normalizer, WorkflowComparator(), WorkflowAnalyzer()


def analyze(before_source, after_source, pipeline):
    parser, normalizer, comparator, analyzer = pipeline
    before = normalizer.normalize(parser.parse(before_source))
    after = normalizer.normalize(parser.parse(after_source))
    comparison = comparator.compare(before, after)
    return before, after, comparison, analyzer.analyze(before, after, comparison)


BASE = """name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Test
        run: pytest
"""


def test_permission_broadening_is_high_confidence_security_finding(pipeline):
    before = BASE.replace("on: push", "on: push\npermissions:\n  contents: read")
    after = BASE.replace("on: push", "on: push\npermissions:\n  contents: write")

    _, _, _, findings = analyze(before, after, pipeline)

    finding = next(item for item in findings if item.rule_id == "permissions-broadened")
    assert finding.severity == "high"
    assert finding.confidence == "high"
    assert "contents" in " ".join(finding.evidence)
    assert finding.recommendation


def test_permission_reduction_does_not_create_broadening_finding(pipeline):
    before = BASE.replace("on: push", "on: push\npermissions:\n  contents: write")
    after = BASE.replace("on: push", "on: push\npermissions:\n  contents: read")

    _, _, _, findings = analyze(before, after, pipeline)

    assert not any(item.rule_id == "permissions-broadened" for item in findings)


def test_action_tag_change_gets_supply_chain_finding(pipeline):
    before = BASE.replace(
        "      - name: Test\n        run: pytest",
        "      - name: Checkout\n        uses: actions/checkout@v4\n"
        "      - name: Test\n        run: pytest",
    )
    after = before.replace("actions/checkout@v4", "actions/checkout@v5")

    _, _, _, findings = analyze(before, after, pipeline)

    finding = next(
        item for item in findings if item.rule_id == "action-reference-not-pinned"
    )
    assert finding.severity == "medium"
    assert finding.confidence == "high"
    assert finding.path.endswith(".uses")


def test_sha_pinned_action_change_does_not_get_unpinned_finding(pipeline):
    old_sha = "0123456789abcdef0123456789abcdef01234567"
    new_sha = "abcdef0123456789abcdef0123456789abcdef01"
    before = BASE.replace(
        "      - name: Test\n        run: pytest",
        f"      - uses: actions/checkout@{old_sha}\n"
        "      - name: Test\n        run: pytest",
    )
    after = before.replace(old_sha, new_sha)

    _, _, _, findings = analyze(before, after, pipeline)

    assert not any(item.rule_id == "action-reference-not-pinned" for item in findings)


def test_pull_request_target_checkout_and_run_requires_review(pipeline):
    before = BASE
    after = BASE.replace(
        "on: push",
        "on: [push, pull_request_target]",
    ).replace(
        "    steps:\n      - name: Test",
        "    steps:\n      - name: Checkout\n        uses: actions/checkout@v4\n"
        "      - name: Test",
    )

    _, _, _, findings = analyze(before, after, pipeline)

    finding = next(
        item
        for item in findings
        if item.rule_id == "privileged-pull-request-execution"
    )
    assert finding.severity == "high"
    assert finding.confidence == "medium"
    assert any("shell run step" in evidence for evidence in finding.evidence)


def test_invalid_comparison_produces_no_findings(pipeline):
    parser, normalizer, comparator, analyzer = pipeline
    before = normalizer.normalize(parser.parse(BASE))
    invalid = normalizer.normalize(parser.parse("not: [valid"))
    comparison = comparator.compare(before, invalid)

    assert analyzer.analyze(before, invalid, comparison) == ()


def test_findings_are_deterministic_and_unique(pipeline):
    before = BASE.replace("on: push", "on: push\npermissions:\n  contents: read")
    after = BASE.replace("on: push", "on: push\npermissions:\n  contents: write")

    first = analyze(before, after, pipeline)[3]
    second = analyze(before, after, pipeline)[3]

    assert first == second
    assert len({(item.rule_id, item.path) for item in first}) == len(first)


def test_missing_permissions_to_read_all_is_flagged_as_broadening(pipeline):
    before = BASE
    after = BASE.replace("on: push", "on: push\npermissions: read-all")

    _, _, _, findings = analyze(before, after, pipeline)

    assert any(item.rule_id == "permissions-broadened" for item in findings)


def test_existing_privileged_trigger_reports_new_checkout_change(pipeline):
    before = BASE.replace("on: push", "on: [push, pull_request_target]")
    after = before.replace(
        "    steps:\n      - name: Test",
        "    steps:\n      - name: Checkout\n        uses: actions/checkout@v4\n"
        "      - name: Test",
    )

    _, _, _, findings = analyze(before, after, pipeline)

    assert any(
        item.rule_id == "privileged-pull-request-execution" for item in findings
    )
