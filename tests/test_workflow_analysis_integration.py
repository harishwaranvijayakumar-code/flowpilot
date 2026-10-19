from workflow_parser.analysis import WorkflowAnalyzer
from workflow_parser.comparison import WorkflowComparator
from workflow_parser.normalizer import WorkflowNormalizer
from workflow_parser.parser import WorkflowParser


def analyze_workflow_change(before_yaml: str, after_yaml: str):
    """Run the workflow analysis pipeline end to end."""
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

    return comparison, result


def test_pipeline_detects_added_write_permission():
    before_yaml = """
name: CI
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    after_yaml = """
name: CI
on: push
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    comparison, result = analyze_workflow_change(before_yaml, after_yaml)

    assert comparison.valid
    assert result.valid

    finding = next(
        item
        for item in result.findings
        if item.rule_id == "permissions.write_scope_added"
    )

    assert finding.severity == "high"
    assert finding.evidence[0].path == "permissions"
    assert finding.location is not None


def test_pipeline_detects_job_level_write_permission():
    before_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
"""

    after_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
"""

    comparison, result = analyze_workflow_change(before_yaml, after_yaml)

    assert comparison.valid
    assert result.valid

    finding = next(
        item
        for item in result.findings
        if item.rule_id == "permissions.write_scope_added"
    )

    assert finding.evidence[0].path == "jobs.build.permissions"
    assert finding.location is not None


def test_pipeline_detects_pull_request_target_added():
    before_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    after_yaml = """
name: CI
on:
  push:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    comparison, result = analyze_workflow_change(before_yaml, after_yaml)

    assert comparison.valid
    assert result.valid
    assert any(
        finding.rule_id == "triggers.pull_request_target_added"
        for finding in result.findings
    )


def test_pipeline_detects_changed_action_reference():
    before_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
"""

    after_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v5
"""

    comparison, result = analyze_workflow_change(before_yaml, after_yaml)

    assert comparison.valid
    assert result.valid

    finding = next(
        item for item in result.findings if item.rule_id == "actions.reference_changed"
    )

    assert finding.severity == "medium"
    assert finding.evidence[0].path.endswith(".uses")


def test_pipeline_reports_no_findings_for_identical_workflows():
    workflow_yaml = """
name: CI
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    comparison, result = analyze_workflow_change(
        workflow_yaml,
        workflow_yaml,
    )

    assert comparison.valid
    assert result.valid
    assert comparison.changes == ()
    assert result.findings == ()


def test_pipeline_preserves_deterministic_output():
    before_yaml = """
name: CI
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    after_yaml = """
name: CI
on:
  push:
  pull_request_target:
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
"""

    _, first = analyze_workflow_change(before_yaml, after_yaml)
    _, second = analyze_workflow_change(before_yaml, after_yaml)

    assert first.to_dict() == second.to_dict()


def test_pipeline_detects_added_action_reference():
    before_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Test
        run: pytest
"""

    after_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
"""

    comparison, result = analyze_workflow_change(before_yaml, after_yaml)

    assert comparison.valid
    assert result.valid

    finding = next(
        item for item in result.findings if item.rule_id == "actions.reference_added"
    )

    assert finding.severity == "medium"
    assert finding.evidence[0].change_type == "added"
    assert finding.evidence[0].after["uses"] == "actions/checkout@v4"
    assert finding.location is not None
