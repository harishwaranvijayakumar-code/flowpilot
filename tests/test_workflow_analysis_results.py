from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from jobs.analysis_handler import (
    WorkflowAnalysisOutput,
    analyze_workflow_payload,
)
from jobs.analysis_results import persist_workflow_result_and_complete
from jobs.models import AnalysisJob, WorkflowAnalysisResult
from jobs.worker import claim_next_job


@pytest.mark.django_db
def test_persist_result_completes_job_atomically():
    job = AnalysisJob.objects.create(
        job_type="workflow_analysis",
        dedupe_key="analysis-result-success",
        payload={},
    )
    claim = claim_next_job()
    assert claim is not None
    assert claim.job_id == job.id

    output = WorkflowAnalysisOutput(
        valid=True,
        findings=[{"rule_id": "actions.reference_changed"}],
        diagnostics=[],
    )

    assert persist_workflow_result_and_complete(claim, output) is True

    job.refresh_from_db()
    result = WorkflowAnalysisResult.objects.get(job=job)

    assert job.status == AnalysisJob.Status.COMPLETED
    assert job.lease_token is None
    assert job.lease_until is None
    assert result.schema_version == 1
    assert result.valid is True
    assert result.findings == [{"rule_id": "actions.reference_changed"}]
    assert result.diagnostics == []


@pytest.mark.django_db
def test_stale_claim_cannot_persist_result():
    job = AnalysisJob.objects.create(
        job_type="workflow_analysis",
        dedupe_key="analysis-result-stale",
    )
    first_claim = claim_next_job()
    assert first_claim is not None

    AnalysisJob.objects.filter(id=job.id).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )

    second_claim = claim_next_job()
    assert second_claim is not None
    assert second_claim.lease_token != first_claim.lease_token

    output = WorkflowAnalysisOutput(
        valid=True,
        findings=[{"rule_id": "stale"}],
        diagnostics=[],
    )

    assert persist_workflow_result_and_complete(first_claim, output) is False
    assert not WorkflowAnalysisResult.objects.filter(job=job).exists()

    assert persist_workflow_result_and_complete(second_claim, output) is True

    result = WorkflowAnalysisResult.objects.get(job=job)
    assert result.findings == [{"rule_id": "stale"}]


@pytest.mark.django_db
def test_invalid_analysis_result_is_persisted_as_invalid():
    job = AnalysisJob.objects.create(
        job_type="workflow_analysis",
        dedupe_key="analysis-result-invalid",
    )
    claim = claim_next_job()
    assert claim is not None

    output = WorkflowAnalysisOutput(
        valid=False,
        findings=[],
        diagnostics=[
            {
                "code": "invalid_yaml",
                "message": "Workflow YAML is invalid.",
            }
        ],
    )

    assert persist_workflow_result_and_complete(claim, output) is True

    result = WorkflowAnalysisResult.objects.get(job=job)
    assert result.valid is False
    assert result.findings == []
    assert result.diagnostics[0]["code"] == "invalid_yaml"


def test_analysis_payload_requires_both_yaml_strings():
    with pytest.raises(ValueError, match="before_yaml"):
        analyze_workflow_payload({"after_yaml": "name: CI"})


def test_analysis_payload_rejects_non_string_yaml():
    with pytest.raises(ValueError, match="before_yaml"):
        analyze_workflow_payload(
            {
                "before_yaml": None,
                "after_yaml": "name: CI",
            }
        )


def test_analysis_payload_runs_end_to_end():
    workflow_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    output = analyze_workflow_payload(
        {
            "before_yaml": workflow_yaml,
            "after_yaml": workflow_yaml,
        }
    )

    assert output.valid is True
    assert output.findings == []
    assert output.diagnostics == []
    assert output.schema_version == 1


@pytest.mark.django_db
def test_worker_processes_workflow_analysis_job_end_to_end():
    from jobs.management.commands.run_worker import process_claimed_job

    workflow_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    job = AnalysisJob.objects.create(
        job_type="workflow_analysis",
        dedupe_key="analysis-worker-end-to-end",
        payload={
            "before_yaml": workflow_yaml,
            "after_yaml": workflow_yaml,
        },
    )

    claim = claim_next_job()
    assert claim is not None
    assert claim.job_id == job.id

    process_claimed_job(claim)

    job.refresh_from_db()
    assert job.status == AnalysisJob.Status.COMPLETED

    result = WorkflowAnalysisResult.objects.get(job=job)
    assert result.valid is True
    assert result.findings == []
    assert result.diagnostics == []


@pytest.mark.django_db
def test_worker_schedules_retry_when_result_persistence_fails():
    from jobs.management.commands.run_worker import process_claimed_job

    workflow_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    job = AnalysisJob.objects.create(
        job_type="workflow_analysis",
        dedupe_key="analysis-result-persistence-failure",
        payload={
            "before_yaml": workflow_yaml,
            "after_yaml": workflow_yaml,
        },
    )

    claim = claim_next_job()
    assert claim is not None
    assert claim.job_id == job.id

    with patch(
        "jobs.management.commands.run_worker.persist_workflow_result_and_complete",
        side_effect=RuntimeError("Database temporarily unavailable"),
    ):
        process_claimed_job(claim)

    job.refresh_from_db()

    assert job.status == AnalysisJob.Status.RETRY_WAIT
    assert job.lease_token is None
    assert job.lease_until is None
    assert "Database temporarily unavailable" in job.last_error
    assert not WorkflowAnalysisResult.objects.filter(job=job).exists()


@pytest.mark.django_db
def test_worker_survives_retry_scheduling_failure():
    from jobs.management.commands.run_worker import process_claimed_job

    workflow_yaml = """
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

    job = AnalysisJob.objects.create(
        job_type="workflow_analysis",
        dedupe_key="analysis-retry-scheduling-failure",
        payload={
            "before_yaml": workflow_yaml,
            "after_yaml": workflow_yaml,
        },
    )

    claim = claim_next_job()
    assert claim is not None
    assert claim.job_id == job.id

    with (
        patch(
            "jobs.management.commands.run_worker.persist_workflow_result_and_complete",
            side_effect=RuntimeError("Database temporarily unavailable"),
        ),
        patch(
            "jobs.management.commands.run_worker.fail_job",
            side_effect=RuntimeError("Retry scheduling unavailable"),
        ),
    ):
        process_claimed_job(claim)

    job.refresh_from_db()

    # The worker must not falsely mark the job completed or lose its lease.
    # Lease expiry and recovery will make it eligible for subsequent handling.
    assert job.status == AnalysisJob.Status.RUNNING
    assert job.lease_token == claim.lease_token
    assert job.lease_until is not None
    assert not WorkflowAnalysisResult.objects.filter(job=job).exists()
