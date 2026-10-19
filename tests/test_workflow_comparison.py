import pytest

from workflow_parser.comparison import WorkflowComparator
from workflow_parser.normalizer import WorkflowNormalizer
from workflow_parser.parser import WorkflowParser


@pytest.fixture
def parser():
    return WorkflowParser()


@pytest.fixture
def normalizer():
    return WorkflowNormalizer()


@pytest.fixture
def comparator():
    return WorkflowComparator()


def normalize(source, parser, normalizer):
    return normalizer.normalize(parser.parse(source))


def test_identical_workflows_produce_no_changes(parser, normalizer, comparator):
    source = """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""

    before = normalize(source, parser, normalizer)
    after = normalize(source, parser, normalizer)

    result = comparator.compare(before, after)

    assert result.valid
    assert result.changes == ()
    assert result.diagnostics == ()


def test_detects_added_and_removed_triggers(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert {
        (change.category, change.change_type, change.path) for change in result.changes
    } == {
        ("trigger", "removed", "on.push"),
        ("trigger", "added", "on.pull_request"),
    }


def test_detects_trigger_filter_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on:
  push:
    branches: [main, release]
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(item for item in result.changes if item.path == "on.push.branches")
    assert change.category == "trigger_filter"
    assert change.change_type == "modified"
    assert change.before == ["main"]
    assert change.after == ["main", "release"]


def test_detects_added_and_removed_jobs(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert any(
        change.path == "jobs.build" and change.change_type == "added"
        for change in result.changes
    )
    assert not any(change.path == "jobs.test" for change in result.changes)


def test_detects_job_runner_dependency_condition_and_permission_changes(
    parser, normalizer, comparator
):
    before = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/main'
    permissions:
      contents: read
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: windows-latest
  test:
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/release'
    permissions:
      contents: write
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    changed_paths = {change.path for change in result.changes}

    assert "jobs.build.runs-on" in changed_paths
    assert "jobs.test.if" in changed_paths
    assert "jobs.test.permissions" in changed_paths

    permission_change = next(
        change for change in result.changes if change.path == "jobs.test.permissions"
    )
    assert permission_change.category == "permission"


def test_detects_added_removed_and_modified_steps(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest -q
      - name: Package
        run: echo package
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert any(
        change.path == "jobs.test.steps[1].run" and change.change_type == "modified"
        for change in result.changes
    )
    assert any(
        change.path == "jobs.test.steps[2]" and change.change_type == "added"
        for change in result.changes
    )


def test_inserting_step_at_beginning_does_not_modify_existing_steps(
    parser, normalizer, comparator
):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
      - name: Package
        run: python -m build
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Setup
        run: echo setup
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
      - name: Package
        run: python -m build
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert len(result.changes) == 1
    assert result.changes[0].change_type == "added"
    assert result.changes[0].path == "jobs.test.steps[0]"
    assert result.changes[0].after["name"] == "Setup"


def test_inserting_step_in_middle_does_not_cascade_modifications(
    parser, normalizer, comparator
):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
      - name: Package
        run: python -m build
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Lint
        run: ruff check .
      - name: Test
        run: pytest
      - name: Package
        run: python -m build
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert len(result.changes) == 1
    assert result.changes[0].change_type == "added"
    assert result.changes[0].path == "jobs.test.steps[1]"
    assert result.changes[0].after["name"] == "Lint"


def test_removing_step_does_not_cascade_modifications(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Lint
        run: ruff check .
      - name: Test
        run: pytest
      - name: Package
        run: python -m build
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
      - name: Package
        run: python -m build
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert len(result.changes) == 1
    assert result.changes[0].change_type == "removed"
    assert result.changes[0].path == "jobs.test.steps[1]"
    assert result.changes[0].before["name"] == "Lint"


def test_modified_step_is_matched_after_an_insertion(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Setup
        run: echo setup
      - name: Checkout
        uses: actions/checkout@v4
      - name: Test
        run: pytest -q
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert {(change.change_type, change.path) for change in result.changes} == {
        ("added", "jobs.test.steps[0]"),
        ("modified", "jobs.test.steps[2].run"),
    }


def test_step_id_is_used_to_match_a_modified_step(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: test
        run: pytest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: test
        run: pytest -q
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert len(result.changes) == 1
    assert result.changes[0].path == "jobs.test.steps[0].run"
    assert result.changes[0].change_type == "modified"


def test_conflicting_explicit_step_ids_are_not_matched(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: old_step
        name: Test
        run: pytest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: new_step
        name: Test
        run: pytest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert {change.change_type for change in result.changes} == {"added", "removed"}
    assert len(result.changes) == 2


def test_invalid_workflow_is_not_compared(parser, normalizer, comparator):
    valid = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )
    invalid = normalize(
        """
name: Invalid
on:
  push: true
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(valid, invalid)

    assert not result.valid
    assert result.changes == ()
    assert result.diagnostics


def test_comparison_order_is_deterministic(parser, normalizer, comparator):
    before_source = """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
    after_source = """
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  build:
    runs-on: windows-latest
  test:
    runs-on: windows-latest
    steps:
      - run: pytest -q
"""

    before = normalize(before_source, parser, normalizer)
    after = normalize(after_source, parser, normalizer)

    first = comparator.compare(before, after)
    second = comparator.compare(before, after)

    assert first == second
    assert list(first.changes) == sorted(
        first.changes,
        key=lambda change: (
            change.path,
            change.change_type,
            change.message,
        ),
    )


def test_comparison_preserves_source_location(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: windows-latest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)
    change = next(item for item in result.changes if item.path == "jobs.test.runs-on")

    assert change.source_location is not None
    assert change.source_location.line is not None


def test_detects_workflow_permission_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
permissions:
  contents: write
jobs:
  test:
    runs-on: ubuntu-latest
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(item for item in result.changes if item.path == "permissions")
    assert change.category == "permission"
    assert change.change_type == "modified"
    assert change.before == {"contents": "read"}
    assert change.after == {"contents": "write"}


def test_detects_job_dependency_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
    needs: build
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  lint:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
    needs: [build, lint]
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(item for item in result.changes if item.path == "jobs.test.needs")
    assert change.category == "dependency"
    assert change.change_type == "modified"
    assert change.before == ["build"]
    assert change.after == ["build", "lint"]


def test_dependency_order_does_not_create_false_change(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  lint:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
    needs: [build, lint]
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  lint:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
    needs: [lint, build]
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    assert not any(change.path == "jobs.test.needs" for change in result.changes)


def test_detects_strategy_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12"]
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12", "3.13"]
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(item for item in result.changes if item.path == "jobs.test.strategy")
    assert change.category == "job"
    assert change.change_type == "modified"


def test_detects_environment_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: staging
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(
        item for item in result.changes if item.path == "jobs.deploy.environment"
    )
    assert change.category == "job"
    assert change.change_type == "modified"
    assert change.before == "staging"
    assert change.after == "production"


def test_detects_step_input_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 1
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(
        item for item in result.changes if item.path == "jobs.test.steps[0].with"
    )
    assert change.category == "step"
    assert change.change_type == "modified"
    assert change.before == {"fetch-depth": 1}
    assert change.after == {"fetch-depth": 0}


def test_detects_step_environment_changes(parser, normalizer, comparator):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Test
        run: pytest
        env:
          LOG_LEVEL: info
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Test
        run: pytest
        env:
          LOG_LEVEL: debug
""",
        parser,
        normalizer,
    )

    result = comparator.compare(before, after)

    assert result.valid
    change = next(
        item for item in result.changes if item.path == "jobs.test.steps[0].env"
    )
    assert change.category == "step"
    assert change.change_type == "modified"
    assert change.before == {"LOG_LEVEL": "info"}
    assert change.after == {"LOG_LEVEL": "debug"}


def test_repeated_identical_steps_align_deterministically(
    parser, normalizer, comparator
):
    before = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Check
        run: pytest
      - name: Check
        run: pytest
""",
        parser,
        normalizer,
    )
    after = normalize(
        """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Setup
        run: echo setup
      - name: Check
        run: pytest
      - name: Check
        run: pytest
""",
        parser,
        normalizer,
    )

    first = comparator.compare(before, after)
    second = comparator.compare(before, after)

    assert first == second
    assert len(first.changes) == 1
    assert first.changes[0].change_type == "added"
    assert first.changes[0].path == "jobs.test.steps[0]"


def test_duplicate_step_ids_do_not_crash_comparison(parser, normalizer, comparator):
    source = """
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: repeated
        run: echo first
      - id: repeated
        run: echo second
"""

    workflow = normalize(source, parser, normalizer)
    result = comparator.compare(workflow, workflow)

    assert result.valid
    assert result.changes == ()
