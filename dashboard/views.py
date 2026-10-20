from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from dashboard.models import DashboardMembership
from github_integration.models import GitHubInstallation, Repository
from jobs.models import AnalysisJob, WorkflowAnalysisResult

PR_ANALYSIS_JOB_TYPE = "analyze_pull_request"


def _accessible_repositories(user):
    repository_ids = DashboardMembership.objects.filter(
        user=user,
    ).values("repository_id")

    return Repository.objects.filter(
        pk__in=repository_ids,
        status=Repository.Status.ACTIVE,
        installation__status=GitHubInstallation.Status.ACTIVE,
    ).select_related("installation")


def _manageable_repositories(user):
    """Return repositories this user is allowed to manage."""
    active = Repository.objects.filter(
        status=Repository.Status.ACTIVE,
        installation__status=GitHubInstallation.Status.ACTIVE,
    ).select_related("installation")

    if user.is_staff or user.is_superuser:
        return active

    maintainer_ids = DashboardMembership.objects.filter(
        user=user,
        role=DashboardMembership.Role.MAINTAINER,
    ).values("repository_id")

    return active.filter(pk__in=maintainer_ids)


def _can_manage_any_repository(user):
    if user.is_staff or user.is_superuser:
        return True

    return _manageable_repositories(user).exists()


def _accessible_pr_jobs(user):
    repository_ids = list(
        _accessible_repositories(user).values_list(
            "github_repo_id",
            flat=True,
        )
    )

    if not repository_ids:
        return AnalysisJob.objects.none()

    return AnalysisJob.objects.filter(
        job_type=PR_ANALYSIS_JOB_TYPE,
        payload__repository_id__in=repository_ids,
    ).order_by("-created_at")


def _safe_items(value):
    """Keep only dictionary entries from a JSON list."""
    if not isinstance(value, list):
        return []

    return [item for item in value if isinstance(item, dict)]


def _status_class(status):
    return {
        AnalysisJob.Status.COMPLETED: "status-success",
        AnalysisJob.Status.FAILED: "status-error",
        AnalysisJob.Status.RUNNING: "status-running",
        AnalysisJob.Status.RETRY_WAIT: "status-warning",
        AnalysisJob.Status.QUEUED: "status-neutral",
    }.get(status, "status-neutral")


@login_required
def index(request):
    repositories = _accessible_repositories(request.user).order_by("full_name")
    repository_ids = list(repositories.values_list("github_repo_id", flat=True))

    jobs = (
        AnalysisJob.objects.filter(payload__repository_id__in=repository_ids)
        if repository_ids
        else AnalysisJob.objects.none()
    )

    job_counts = jobs.aggregate(
        total=Count("id"),
        queued=Count("id", filter=Q(status=AnalysisJob.Status.QUEUED)),
        running=Count("id", filter=Q(status=AnalysisJob.Status.RUNNING)),
        retrying=Count("id", filter=Q(status=AnalysisJob.Status.RETRY_WAIT)),
        completed=Count("id", filter=Q(status=AnalysisJob.Status.COMPLETED)),
        failed=Count("id", filter=Q(status=AnalysisJob.Status.FAILED)),
    )

    results = WorkflowAnalysisResult.objects.filter(job__in=jobs).only("findings")
    findings_count = sum(len(_safe_items(result.findings)) for result in results)

    context = {
        "repositories": repositories,
        "repository_count": repositories.count(),
        "installation_count": (
            repositories.values("installation_id").distinct().count()
        ),
        "job_counts": job_counts,
        "findings_count": findings_count,
        "recent_jobs": jobs.order_by("-created_at")[:10],
        "can_manage_repositories": _can_manage_any_repository(request.user),
    }

    return render(request, "dashboard/index.html", context)


@login_required
def pull_requests(request):
    repositories = {
        repository.github_repo_id: repository
        for repository in _accessible_repositories(request.user)
    }

    jobs = list(_accessible_pr_jobs(request.user)[:100])

    results_by_job_id = {
        result.job_id: result
        for result in WorkflowAnalysisResult.objects.filter(
            job_id__in=[job.id for job in jobs],
        )
    }

    for job in jobs:
        payload = job.payload if isinstance(job.payload, dict) else {}
        job.display_repository = repositories.get(payload.get("repository_id"))

        number = payload.get("pull_request_number")
        job.pull_request_number = (
            number
            if isinstance(number, int) and not isinstance(number, bool) and number > 0
            else None
        )

        job.status_css_class = _status_class(job.status)
        result = results_by_job_id.get(job.id)
        job.finding_count = len(_safe_items(result.findings)) if result else 0

    return render(
        request,
        "dashboard/pull_requests.html",
        {
            "jobs": jobs,
            "can_manage_repositories": _can_manage_any_repository(request.user),
        },
    )


@login_required
def analysis_detail(request, job_id):
    job = get_object_or_404(
        _accessible_pr_jobs(request.user),
        pk=job_id,
    )

    payload = job.payload if isinstance(job.payload, dict) else {}
    repository = get_object_or_404(
        _accessible_repositories(request.user),
        github_repo_id=payload.get("repository_id"),
    )

    result = WorkflowAnalysisResult.objects.filter(job=job).first()
    findings = _safe_items(result.findings) if result else []
    diagnostics = _safe_items(result.diagnostics) if result else []

    raw_number = payload.get("pull_request_number")
    pull_request_number = (
        raw_number
        if isinstance(raw_number, int)
        and not isinstance(raw_number, bool)
        and raw_number > 0
        else None
    )

    pull_request_url = (
        f"{repository.html_url.rstrip('/')}/pull/{pull_request_number}"
        if pull_request_number
        else None
    )

    return render(
        request,
        "dashboard/analysis_detail.html",
        {
            "job": job,
            "repository": repository,
            "pull_request_number": pull_request_number,
            "pull_request_url": pull_request_url,
            "head_sha": (
                payload.get("head_sha")
                if isinstance(payload.get("head_sha"), str)
                else ""
            ),
            "base_sha": (
                payload.get("base_sha")
                if isinstance(payload.get("base_sha"), str)
                else ""
            ),
            "result": result,
            "findings": findings,
            "diagnostics": diagnostics,
            "status_css_class": _status_class(job.status),
            "can_manage_repositories": _can_manage_any_repository(request.user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def repository_access(request):
    """
    Manage memberships for active repositories.

    Staff can manage all active repositories. Maintainers can manage only
    repositories where they already hold the maintainer role.
    """
    repositories = _manageable_repositories(request.user).order_by("full_name")

    if not repositories.exists():
        raise PermissionDenied(
            "Repository access management requires staff access or "
            "maintainer membership on an active repository."
        )

    if request.method == "POST":
        action = request.POST.get("action", "")
        repository_id = request.POST.get("repository_id", "")

        repository = get_object_or_404(repositories, pk=repository_id)

        if action == "grant":
            user_id = request.POST.get("user_id", "")
            role = request.POST.get("role", "")

            valid_roles = {
                DashboardMembership.Role.VIEWER,
                DashboardMembership.Role.MAINTAINER,
            }
            if role not in valid_roles:
                messages.error(request, "Choose a valid membership role.")
                return redirect("dashboard:repository_access")

            User = get_user_model()
            target_user = get_object_or_404(User, pk=user_id)

            DashboardMembership.objects.update_or_create(
                user=target_user,
                repository=repository,
                defaults={"role": role},
            )
            messages.success(
                request,
                f"Access for {target_user.get_username()} was saved.",
            )

        elif action == "revoke":
            user_id = request.POST.get("user_id", "")
            User = get_user_model()
            target_user = get_object_or_404(User, pk=user_id)

            with transaction.atomic():
                memberships = DashboardMembership.objects.select_for_update().filter(
                    repository=repository,
                )
                membership = get_object_or_404(
                    memberships,
                    user=target_user,
                )

                if membership.role == DashboardMembership.Role.MAINTAINER:
                    remaining_maintainers = (
                        memberships.filter(
                            role=DashboardMembership.Role.MAINTAINER,
                        )
                        .exclude(pk=membership.pk)
                        .count()
                    )

                    if remaining_maintainers == 0:
                        messages.error(
                            request,
                            "A repository must retain at least one maintainer.",
                        )
                        return redirect("dashboard:repository_access")

                membership.delete()

            messages.success(
                request,
                f"Access for {target_user.get_username()} was removed.",
            )

        else:
            messages.error(request, "Unknown repository access action.")

        return redirect("dashboard:repository_access")

    memberships = DashboardMembership.objects.filter(
        repository__in=repositories,
    ).select_related("user", "repository")

    users = get_user_model().objects.order_by("username")

    return render(
        request,
        "dashboard/repository_access.html",
        {
            "repositories": repositories,
            "memberships": memberships,
            "users": users,
            "role_choices": DashboardMembership.Role.choices,
            "can_manage_repositories": True,
        },
    )
