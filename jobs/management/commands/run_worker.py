import logging
import signal
import threading

from django.core.management.base import BaseCommand, CommandError

from jobs.analysis_handler import WorkflowAnalysisOutput, analyze_workflow_payload
from jobs.analysis_results import persist_workflow_result_and_complete
from jobs.worker import (
    claim_next_job,
    complete_job,
    fail_job,
    recover_exhausted_jobs,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 1.0

JOB_HANDLERS = {
    "workflow_analysis": lambda claim: analyze_workflow_payload(claim.payload),
}


def process_claimed_job(claim):
    """Execute a claimed job and preserve its lease guarantees."""
    handler = JOB_HANDLERS.get(claim.job_type)

    if handler is None:
        message = f"No handler registered for job type: {claim.job_type}"
        fail_job(claim, message, retryable=False)
        logger.error("Job %s failed: %s", claim.job_id, message)
        return

    try:
        outcome = handler(claim)
    except Exception as exc:
        logger.exception("Job %s failed during processing", claim.job_id)
        fail_job(claim, str(exc), retryable=True)
        return

    if isinstance(outcome, WorkflowAnalysisOutput):
        try:
            completed = persist_workflow_result_and_complete(claim, outcome)
        except Exception as exc:
            logger.exception(
                "Job %s failed while persisting its analysis result",
                claim.job_id,
            )
            try:
                failed = fail_job(claim, str(exc), retryable=True)
            except Exception:
                logger.exception(
                    "Could not schedule retry for job %s",
                    claim.job_id,
                )
                return

            if not failed:
                logger.warning(
                    "Job %s could not be scheduled for retry because "
                    "its lease is no longer valid",
                    claim.job_id,
                )
            return
    else:
        completed = complete_job(claim)

    if not completed:
        logger.warning(
            "Job %s could not be completed because its lease is no longer valid",
            claim.job_id,
        )
        return

    logger.info("Job %s completed", claim.job_id)


def run_worker_loop(
    stop_event,
    poll_interval=DEFAULT_POLL_INTERVAL,
    lease_seconds=120,
    once=False,
):
    """Recover exhausted jobs and process available work until stopped."""
    while not stop_event.is_set():
        try:
            recovered = recover_exhausted_jobs()
            if recovered:
                logger.warning("Marked %s exhausted jobs as failed", recovered)

            claim = claim_next_job(lease_seconds=lease_seconds)

            if claim is not None:
                process_claimed_job(claim)

                if once:
                    return

                continue

            if once:
                return

        except Exception:
            logger.exception("Worker iteration failed")

            if once:
                raise

        stop_event.wait(poll_interval)


class Command(BaseCommand):
    help = "Run the FlowPilot durable job worker."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=DEFAULT_POLL_INTERVAL,
            help="Seconds to wait when no job is available (default: 1.0).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=int,
            default=120,
            help="Duration of each job lease in seconds (default: 120).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one job and exit.",
        )

    def handle(self, *args, **options):
        poll_interval = options["poll_interval"]
        lease_seconds = options["lease_seconds"]
        once = options["once"]

        if poll_interval <= 0:
            raise CommandError("--poll-interval must be greater than zero.")

        if lease_seconds <= 0:
            raise CommandError("--lease-seconds must be greater than zero.")

        stop_event = threading.Event()
        previous_handlers = {}

        def request_shutdown(signum, frame):
            logger.info("Received signal %s; shutting down", signum)
            stop_event.set()

        if not once:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(
                    signum,
                    request_shutdown,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "FlowPilot worker started."
                if not once
                else "FlowPilot worker running once."
            )
        )

        try:
            run_worker_loop(
                stop_event=stop_event,
                poll_interval=poll_interval,
                lease_seconds=lease_seconds,
                once=once,
            )
        finally:
            for signum, previous_handler in previous_handlers.items():
                signal.signal(signum, previous_handler)

        self.stdout.write(self.style.SUCCESS("FlowPilot worker stopped."))
