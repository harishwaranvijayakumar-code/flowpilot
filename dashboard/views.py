from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render

from dashboard.models import DashboardMembership
from github_integration.models import GitHubInstallation, Repository
from jobs.models import AnalysisJob, WorkflowAnalysisResult


@login_required
def index(request):
    memberships = DashboardMembership.objects.filter(
        user=request.user,
    ).select_related("repository", "repository__installation")

    repositories = Repository.objects.filter(
        pk__in=memberships.values("repository_id"),
        status=Repository.Status.ACTIVE,
        installation__status=GitHubInstallation.Status.ACTIVE,
    ).order_by("full_name")

    repository_ids = list(repositories.values_list("github_repo_id", flat=True))

    jobs = AnalysisJob.objects.filter(
        payload__repository_id__in=repository_ids,
    )

    job_counts = jobs.aggregate(
        total=Count("id"),
        queued=Count(
            "id",
            filter=Q(status=AnalysisJob.Status.QUEUED),
        ),
        running=Count(
            "id",
            filter=Q(status=AnalysisJob.Status.RUNNING),
        ),
        retrying=Count(
            "id",
            filter=Q(status=AnalysisJob.Status.RETRY_WAIT),
        ),
        completed=Count(
            "id",
            filter=Q(status=AnalysisJob.Status.COMPLETED),
        ),
        failed=Count(
            "id",
            filter=Q(status=AnalysisJob.Status.FAILED),
        ),
    )

    results = WorkflowAnalysisResult.objects.filter(
        job__in=jobs,
    ).only("findings")

    findings_count = sum(len(result.findings or []) for result in results)

    recent_jobs = jobs.order_by("-created_at")[:10]

    context = {
        "repositories": repositories,
        "repository_count": repositories.count(),
        "installation_count": repositories.values("installation_id").distinct().count(),
        "job_counts": job_counts,
        "findings_count": findings_count,
        "recent_jobs": recent_jobs,
    }

    return render(request, "dashboard/index.html", context)
