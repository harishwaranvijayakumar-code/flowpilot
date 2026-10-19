import pytest

from workflow_parser.parser import WorkflowParser


@pytest.fixture
def parser():
    return WorkflowParser()


def test_parses_valid_workflow_and_preserves_on_key(parser):
    result = parser.parse(
        """
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""
    )

    assert result.valid
    assert "on" in result.data
    assert result.data["jobs"]["test"]["runs-on"] == "ubuntu-latest"
    assert result.locations["jobs.test"].line == 8


def test_reports_malformed_yaml(parser):
    result = parser.parse(
        """
on: [push
jobs:
  test:
    runs-on: ubuntu-latest
"""
    )

    assert not result.valid
    assert result.diagnostics[0].code == "yaml_parse_error"
    assert result.diagnostics[0].line is not None


def test_rejects_non_mapping_root(parser):
    result = parser.parse("- one\n- two\n")

    assert not result.valid
    assert result.diagnostics[0].code == "invalid_root"


def test_rejects_empty_document(parser):
    result = parser.parse("")

    assert not result.valid
    assert result.diagnostics[0].code == "empty_workflow"


def test_reports_missing_triggers(parser):
    result = parser.parse(
        """
jobs:
  test:
    runs-on: ubuntu-latest
"""
    )

    assert not result.valid
    assert any(
        diagnostic.code == "missing_triggers" for diagnostic in result.diagnostics
    )


def test_reports_unknown_job_dependency(parser):
    result = parser.parse(
        """
on: push
jobs:
  test:
    needs: build
    runs-on: ubuntu-latest
"""
    )

    assert not result.valid
    assert any(
        diagnostic.code == "unknown_dependency" for diagnostic in result.diagnostics
    )


def test_rejects_duplicate_yaml_keys(parser):
    result = parser.parse(
        """
on: push
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
"""
    )

    assert not result.valid
    assert result.diagnostics[0].code == "yaml_parse_error"


def test_reports_invalid_needs_type(parser):
    result = parser.parse(
        """
on: push
jobs:
  test:
    needs: 123
    runs-on: ubuntu-latest
"""
    )

    assert not result.valid
    assert any(diagnostic.code == "invalid_needs" for diagnostic in result.diagnostics)
