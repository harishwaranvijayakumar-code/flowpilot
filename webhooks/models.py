from django.db import models


class WebhookDelivery(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        IGNORED = "ignored", "Ignored"

    delivery_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=100)
    action = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
    )
    payload_sha256 = models.CharField(max_length=64)
    installation_id = models.BigIntegerField(null=True, blank=True)
    repository_id = models.BigIntegerField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(
                fields=["event_type", "received_at"],
                name="webhook_event_received_idx",
            ),
            models.Index(
                fields=["status", "received_at"],
                name="webhook_status_received_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.delivery_id}"
