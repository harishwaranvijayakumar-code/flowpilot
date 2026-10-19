from django.conf import settings
from django.db import models


class DashboardMembership(models.Model):
    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"
        MAINTAINER = "maintainer", "Maintainer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboard_memberships",
    )
    repository = models.ForeignKey(
        "github_integration.Repository",
        on_delete=models.CASCADE,
        related_name="dashboard_memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["repository__full_name", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "repository"],
                name="unique_dashboard_user_repo",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "role"],
                name="membership_user_role_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.repository.full_name} ({self.role})"
