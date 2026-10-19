import pytest

from workflow_parser.normalizer import WorkflowNormalizer
from workflow_parser.parser import WorkflowParser


@pytest.fixture
def parser():
    return WorkflowParser()


@pytest.fixture
def normalizer():
    return WorkflowNormalizer()


def test_normalizes_triggers_jobs_and_steps(parser, normalizer):
    parsed = parser.parse(
        """
name: CI
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo build
  test:
    runs-on: ubuntu-latest
    needs: [build]
    if: ${{ github.event_name == 'pull_request' }}
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: pytest
"""
    )

    result = normalizer.normalize(parsed)

    assert result.valid
    assert result.name == "CI"
    assert [trigger.name for trigger in result.triggers] == ["pull_request"]
    assert result.triggers[0].filters == (("branches", ("main",)),)
    assert result.permissions == (("contents", "read"),)

    job = next(job for job in result.jobs if job.job_id == "test")
    assert job.runs_on == "ubuntu-latest"
    assert job.needs == ("build",)
    assert job.condition == "${{ github.event_name == 'pull_request' }}"
    assert len(job.steps) == 2
    assert job.steps[0].uses == "actions/checkout@v4"
    assert job.steps[1].name == "Run tests"
    assert job.steps[1].run == "pytest"


def test_normalization_orders_jobs_triggers_and_needs(parser, normalizer):
    parsed = parser.parse(
        """
name: Stable
on: [workflow_dispatch, push]
jobs:
  z:
    runs-on: ubuntu-latest
    needs: [b, a]
  b:
    runs-on: ubuntu-latest
  a:
    runs-on: ubuntu-latest
"""
    )

    result = normalizer.normalize(parsed)

    assert result.valid
    assert [trigger.name for trigger in result.triggers] == [
        "push",
        "workflow_dispatch",
    ]
    assert [job.job_id for job in result.jobs] == ["a", "b", "z"]
    assert result.jobs[2].needs == ("a", "b")


def test_normalizes_job_permissions_and_step_inputs(parser, normalizer):
    parsed = parser.parse(
        """
name: Permissions
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: example/build@v1
        with:
          mode: release
        env:
          BUILD_ENV: production
"""
    )

    result = normalizer.normalize(parsed)

    assert result.valid
    job = result.jobs[0]
    assert job.permissions == (("contents", "read"),)
    assert job.steps[0].with_args == (("mode", "release"),)
    assert job.steps[0].env == (("BUILD_ENV", "production"),)


def test_reports_invalid_trigger_configuration(parser, normalizer):
    parsed = parser.parse(
        """
name: Invalid trigger
on:
  push: true
jobs:
  test:
    runs-on: ubuntu-latest
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert any(item.code == "invalid_trigger_config" for item in result.diagnostics)


def test_reports_invalid_workflow_permissions(parser, normalizer):
    parsed = parser.parse(
        """
name: Invalid permissions
on: push
permissions: admin
jobs:
  test:
    runs-on: ubuntu-latest
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert any(item.code == "invalid_permissions" for item in result.diagnostics)


def test_reports_invalid_job_steps(parser, normalizer):
    parsed = parser.parse(
        """
name: Invalid steps
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps: invalid
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert any(item.code == "invalid_steps" for item in result.diagnostics)


def test_retains_source_locations(parser, normalizer):
    parsed = parser.parse(
        """
name: Locations
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Check
        run: pytest
"""
    )

    result = normalizer.normalize(parsed)

    assert result.jobs[0].source_location is not None
    assert result.jobs[0].source_location.line == 6
    assert result.jobs[0].steps[0].source_location is not None


def test_reports_invalid_needs_value(parser, normalizer):
    parsed = parser.parse(
        """
name: Invalid needs
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    needs: [123]
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert any(item.code == "invalid_needs" for item in result.diagnostics)


def test_reports_invalid_job_permissions(parser, normalizer):
    parsed = parser.parse(
        """
name: Invalid job permissions
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: admin
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert any(item.code == "invalid_permissions" for item in result.diagnostics)


@pytest.mark.parametrize("field_name", ["with", "env"])
def test_reports_invalid_step_mapping(parser, normalizer, field_name):
    parsed = parser.parse(
        f"""
name: Invalid step mapping
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo test
        {field_name}: invalid
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert any(item.code == f"invalid_step_{field_name}" for item in result.diagnostics)


def test_nested_configuration_is_normalized_deterministically(parser, normalizer):
    first = parser.parse(
        """
name: Stable
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.12", "3.13"]
"""
    )
    second = parser.parse(
        """
name: Stable
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12", "3.13"]
        os: [ubuntu-latest, windows-latest]
"""
    )

    first_model = normalizer.normalize(first)
    second_model = normalizer.normalize(second)

    assert first_model.valid
    assert second_model.valid
    assert first_model.jobs[0].strategy == second_model.jobs[0].strategy


def test_unknown_dependency_is_reported_only_once(parser, normalizer):
    parsed = parser.parse(
        """
name: Unknown dependency
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    needs: missing
"""
    )

    result = normalizer.normalize(parsed)

    matches = [item for item in result.diagnostics if item.code == "unknown_dependency"]

    assert not result.valid
    assert len(matches) == 1


def test_malformed_configuration_never_normalizes_as_valid(parser, normalizer):
    parsed = parser.parse(
        """
name: Invalid configuration
on:
  push: true
jobs:
  test:
    runs-on: ubuntu-latest
    steps: invalid
"""
    )

    result = normalizer.normalize(parsed)

    assert not result.valid
    assert {item.code for item in result.diagnostics} >= {
        "invalid_trigger_config",
        "invalid_steps",
    }


def test_normalization_is_deterministic(parser, normalizer):
    source = """
name: Deterministic
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - name: Run tests
        run: pytest
"""

    first = normalizer.normalize(parser.parse(source))
    second = normalizer.normalize(parser.parse(source))

    assert first == second
