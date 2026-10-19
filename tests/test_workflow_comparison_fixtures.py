import json
from pathlib import Path

import pytest

from workflow_parser.comparison import WorkflowComparator
from workflow_parser.normalizer import WorkflowNormalizer
from workflow_parser.parser import WorkflowParser

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "workflow_comparison"

PARSER = WorkflowParser()
NORMALIZER = WorkflowNormalizer()
COMPARATOR = WorkflowComparator()


def load_workflow(path: Path):
    source = path.read_text(encoding="utf-8")
    parsed = PARSER.parse(source)
    return NORMALIZER.normalize(parsed)


def load_expected(case_directory: Path):
    return json.loads((case_directory / "expected.json").read_text(encoding="utf-8"))


def actual_result(case_directory: Path):
    before = load_workflow(case_directory / "before.yml")
    after = load_workflow(case_directory / "after.yml")
    result = COMPARATOR.compare(before, after)

    assert result.valid, result.diagnostics

    return [
        {
            "category": change.category,
            "change_type": change.change_type,
            "path": change.path,
            "before": change.before,
            "after": change.after,
            "message": change.message,
        }
        for change in result.changes
    ]


@pytest.mark.parametrize(
    "case_name",
    sorted(path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()),
)
def test_workflow_comparison_fixture(case_name):
    case_directory = FIXTURE_ROOT / case_name

    expected = load_expected(case_directory)
    actual = actual_result(case_directory)

    assert actual == expected


@pytest.mark.parametrize(
    "case_name",
    sorted(path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()),
)
def test_workflow_comparison_fixture_is_deterministic(case_name):
    case_directory = FIXTURE_ROOT / case_name

    first = actual_result(case_directory)
    second = actual_result(case_directory)

    assert first == second
