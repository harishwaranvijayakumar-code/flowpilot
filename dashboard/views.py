from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

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
        AnalysisJob.objects.filter(
            payload__repository_id__in=repository_ids,
        )
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

    recent_jobs = jobs.order_by("-created_at")[:10]

    context = {
        "repositories": repositories,
        "repository_count": repositories.count(),
        "installation_count": (
            repositories.values("installation_id").distinct().count()
        ),
        "job_counts": job_counts,
        "findings_count": findings_count,
        "recent_jobs": recent_jobs,
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
        {"jobs": jobs},
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

    context = {
        "job": job,
        "repository": repository,
        "pull_request_number": pull_request_number,
        "pull_request_url": pull_request_url,
        "head_sha": (
            payload.get("head_sha") if isinstance(payload.get("head_sha"), str) else ""
        ),
        "base_sha": (
            payload.get("base_sha") if isinstance(payload.get("base_sha"), str) else ""
        ),
        "result": result,
        "findings": findings,
        "diagnostics": diagnostics,
        "status_css_class": _status_class(job.status),
    }

    return render(request, "dashboard/analysis_detail.html", context)
