import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class AnalysisJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        RETRY_WAIT = "retry_wait", "Waiting to retry"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=64)
    dedupe_key = models.CharField(max_length=512, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    available_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1),
                name="analysis_job_max_attempts_positive",
            ),
            models.CheckConstraint(
                condition=Q(attempts__lte=models.F("max_attempts")),
                name="analysis_job_attempts_lte_max",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status="running")
                    | (Q(lease_until__isnull=False) & Q(lease_token__isnull=False))
                ),
                name="running_analysis_job_has_lease",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "available_at", "created_at"],
                name="analysis_job_queue_idx",
            ),
            models.Index(
                fields=["status", "lease_until"],
                name="analysis_job_lease_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.job_type}:{self.id} ({self.status})"
