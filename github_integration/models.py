from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class GitHubIdentity(models.Model):
    """Link a FlowPilot user to a stable GitHub account identity."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="github_identity",
    )
    github_user_id = models.BigIntegerField(
        unique=True,
        validators=[MinValueValidator(1)],
    )
    login = models.CharField(max_length=255)
    avatar_url = models.URLField(max_length=500, blank=True)
    html_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["login"]
        constraints = [
            models.CheckConstraint(
                condition=Q(github_user_id__gt=0),
                name="github_identity_user_id_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["login"],
                name="github_identity_login_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.login} (GitHub ID: {self.github_user_id})"


class GitHubInstallation(models.Model):
    class AccountType(models.TextChoices):
        USER = "user", "User"
        ORGANIZATION = "organization", "Organization"
        ENTERPRISE = "enterprise", "Enterprise"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        REMOVED = "removed", "Removed"

    installation_id = models.BigIntegerField(
        unique=True,
        validators=[MinValueValidator(1)],
    )
    account_login = models.CharField(max_length=255)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["account_login"]
        constraints = [
            models.CheckConstraint(
                condition=Q(installation_id__gt=0),
                name="github_installation_id_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "account_login"],
                name="gh_install_status_login_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account_login} ({self.installation_id})"


class Repository(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    installation = models.ForeignKey(
        GitHubInstallation,
        on_delete=models.PROTECT,
        related_name="repositories",
    )
    github_repo_id = models.BigIntegerField(
        unique=True,
        validators=[MinValueValidator(1)],
    )
    full_name = models.CharField(max_length=255)
    default_branch = models.CharField(max_length=255, blank=True)
    html_url = models.URLField(max_length=500)
    is_private = models.BooleanField(default=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(github_repo_id__gt=0),
                name="github_repo_id_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["installation", "status"],
                name="repo_install_status_idx",
            ),
            models.Index(
                fields=["status", "full_name"],
                name="repo_status_name_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name
