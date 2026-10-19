import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from dashboard.models import DashboardMembership
from github_integration.models import GitHubInstallation, Repository
from jobs.models import AnalysisJob, WorkflowAnalysisResult


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()

        cls.user = user_model.objects.create_user(
            username="dashboard-user",
            password="test-password",
        )
        cls.other_user = user_model.objects.create_user(
            username="other-user",
            password="test-password",
        )

        cls.installation = GitHubInstallation.objects.create(
            installation_id=1001,
            account_login="flowpilot-org",
            account_type=GitHubInstallation.AccountType.ORGANIZATION,
        )
        cls.other_installation = GitHubInstallation.objects.create(
            installation_id=1002,
            account_login="another-org",
            account_type=GitHubInstallation.AccountType.ORGANIZATION,
        )

        cls.repository = Repository.objects.create(
            installation=cls.installation,
            github_repo_id=2001,
            full_name="flowpilot-org/visible-repository",
            default_branch="main",
            html_url="https://github.com/flowpilot-org/visible-repository",
            is_private=True,
        )
        cls.other_repository = Repository.objects.create(
            installation=cls.other_installation,
            github_repo_id=2002,
            full_name="another-org/private-repository",
            default_branch="main",
            html_url="https://github.com/another-org/private-repository",
            is_private=True,
        )

        DashboardMembership.objects.create(
            user=cls.user,
            repository=cls.repository,
            role=DashboardMembership.Role.VIEWER,
        )
        DashboardMembership.objects.create(
            user=cls.other_user,
            repository=cls.other_repository,
            role=DashboardMembership.Role.MAINTAINER,
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("dashboard:index")

    def create_job(
        self,
        *,
        repository_id,
        dedupe_key,
        status=AnalysisJob.Status.QUEUED,
    ):
        job_fields = {
            "job_type": "pull_request_analysis",
            "dedupe_key": dedupe_key,
            "payload": {"repository_id": repository_id},
            "status": status,
        }

        if status == AnalysisJob.Status.RUNNING:
            job_fields["lease_until"] = timezone.now() + timedelta(minutes=5)
            job_fields["lease_token"] = uuid.uuid4()

        return AnalysisJob.objects.create(**job_fields)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.headers["Location"],
        )

    def test_authenticated_user_sees_only_assigned_repositories(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "flowpilot-org/visible-repository")
        self.assertNotContains(response, "another-org/private-repository")
        self.assertEqual(response.context["repository_count"], 1)

    def test_dashboard_counts_only_jobs_for_assigned_repositories(self):
        visible_queued_job = self.create_job(
            repository_id=self.repository.github_repo_id,
            dedupe_key="visible-queued-job",
            status=AnalysisJob.Status.QUEUED,
        )
        self.create_job(
            repository_id=self.repository.github_repo_id,
            dedupe_key="visible-running-job",
            status=AnalysisJob.Status.RUNNING,
        )
        self.create_job(
            repository_id=self.other_repository.github_repo_id,
            dedupe_key="other-user-job",
            status=AnalysisJob.Status.FAILED,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        counts = response.context["job_counts"]

        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["queued"], 1)
        self.assertEqual(counts["running"], 1)
        self.assertEqual(counts["failed"], 0)

        recent_job_ids = {job.id for job in response.context["recent_jobs"]}
        self.assertIn(visible_queued_job.id, recent_job_ids)
        self.assertEqual(len(recent_job_ids), 2)

    def test_dashboard_counts_findings_for_assigned_repositories(self):
        visible_job = self.create_job(
            repository_id=self.repository.github_repo_id,
            dedupe_key="visible-result-job",
            status=AnalysisJob.Status.COMPLETED,
        )
        hidden_job = self.create_job(
            repository_id=self.other_repository.github_repo_id,
            dedupe_key="hidden-result-job",
            status=AnalysisJob.Status.COMPLETED,
        )

        WorkflowAnalysisResult.objects.create(
            job=visible_job,
            valid=True,
            findings=[
                {"rule": "missing-timeout"},
                {"rule": "unpinned-action"},
            ],
        )
        WorkflowAnalysisResult.objects.create(
            job=hidden_job,
            valid=True,
            findings=[
                {"rule": "private-repository-finding"},
            ],
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["findings_count"], 2)
        self.assertNotContains(
            response,
            "private-repository-finding",
        )

    def test_dashboard_handles_empty_workspace(self):
        DashboardMembership.objects.filter(user=self.user).delete()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["repository_count"], 0)
        self.assertEqual(response.context["job_counts"]["total"], 0)
        self.assertEqual(response.context["findings_count"], 0)
        self.assertContains(response, "Your workspace starts here.")
        self.assertContains(response, "No workflow activity yet.")

    def test_inactive_installation_repository_is_not_displayed(self):
        self.installation.status = GitHubInstallation.Status.SUSPENDED
        self.installation.save(update_fields=["status", "updated_at"])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["repository_count"], 0)
        self.assertNotContains(response, "flowpilot-org/visible-repository")

    def test_login_page_renders(self):
        self.client.logout()

        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Good work")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')


class PullRequestAnalysisViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="pr-viewer",
            password="test-password",
        )
        cls.other_user = user_model.objects.create_user(
            username="other-pr-viewer",
            password="test-password",
        )

        cls.installation = GitHubInstallation.objects.create(
            installation_id=3101,
            account_login="m9-org",
            account_type=GitHubInstallation.AccountType.ORGANIZATION,
        )
        cls.other_installation = GitHubInstallation.objects.create(
            installation_id=3102,
            account_login="private-org",
            account_type=GitHubInstallation.AccountType.ORGANIZATION,
        )

        cls.repository = Repository.objects.create(
            installation=cls.installation,
            github_repo_id=4101,
            full_name="m9-org/workflows",
            default_branch="main",
            html_url="https://github.com/m9-org/workflows",
            is_private=True,
        )
        cls.other_repository = Repository.objects.create(
            installation=cls.other_installation,
            github_repo_id=4102,
            full_name="private-org/secret-workflows",
            default_branch="main",
            html_url="https://github.com/private-org/secret-workflows",
            is_private=True,
        )

        DashboardMembership.objects.create(
            user=cls.user,
            repository=cls.repository,
            role=DashboardMembership.Role.VIEWER,
        )
        DashboardMembership.objects.create(
            user=cls.other_user,
            repository=cls.other_repository,
            role=DashboardMembership.Role.VIEWER,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def create_pr_job(
        self,
        *,
        repository_id=None,
        number=42,
        job_type="analyze_pull_request",
        status=AnalysisJob.Status.QUEUED,
        dedupe_key="m9-pr-job",
    ):
        if repository_id is None:
            repository_id = self.repository.github_repo_id

        return AnalysisJob.objects.create(
            job_type=job_type,
            dedupe_key=dedupe_key,
            status=status,
            payload={
                "installation_id": self.installation.installation_id,
                "repository_id": repository_id,
                "pull_request_number": number,
                "base_sha": "base123",
                "head_sha": "head456",
                "action": "opened",
            },
        )

    def test_anonymous_user_is_redirected_from_pr_list(self):
        self.client.logout()

        response = self.client.get(reverse("dashboard:pull_requests"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.headers["Location"])

    def test_pr_list_only_shows_accessible_pr_analysis_jobs(self):
        self.create_pr_job(dedupe_key="visible-pr")
        self.create_pr_job(
            repository_id=self.other_repository.github_repo_id,
            dedupe_key="hidden-pr",
        )
        self.create_pr_job(
            job_type="publish_check_run",
            dedupe_key="check-run-job",
        )

        response = self.client.get(reverse("dashboard:pull_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "m9-org/workflows")
        self.assertContains(response, "#42")
        self.assertNotContains(response, "private-org/secret-workflows")
        self.assertEqual(len(response.context["jobs"]), 1)

    def test_pr_list_has_an_empty_state(self):
        response = self.client.get(reverse("dashboard:pull_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pull request analyses yet.")

    def test_analysis_detail_displays_findings_and_revision_metadata(self):
        job = self.create_pr_job(status=AnalysisJob.Status.COMPLETED)
        WorkflowAnalysisResult.objects.create(
            job=job,
            valid=True,
            findings=[
                {
                    "severity": "high",
                    "rule_id": "action-unpinned",
                    "title": "Pin third-party actions",
                    "description": "The action uses a mutable reference.",
                    "workflow_path": ".github/workflows/ci.yml",
                    "recommendation": "Pin the action to a full commit SHA.",
                },
            ],
            diagnostics=[
                {
                    "code": "workflow-parsed",
                    "message": "Workflow parsed successfully.",
                    "workflow_path": ".github/workflows/ci.yml",
                },
            ],
        )

        response = self.client.get(
            reverse("dashboard:analysis_detail", kwargs={"job_id": job.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "m9-org/workflows")
        self.assertContains(response, "Pull request #42")
        self.assertContains(response, "Pin third-party actions")
        self.assertContains(response, "action-unpinned")
        self.assertContains(response, "Pin the action to a full commit SHA.")
        self.assertContains(response, "base123")
        self.assertContains(response, "head456")
        self.assertContains(
            response,
            "https://github.com/m9-org/workflows/pull/42",
        )
        self.assertContains(response, "Workflow parsed successfully.")

    def test_user_cannot_view_another_users_analysis(self):
        job = self.create_pr_job(
            repository_id=self.other_repository.github_repo_id,
            dedupe_key="private-analysis",
        )

        response = self.client.get(
            reverse("dashboard:analysis_detail", kwargs={"job_id": job.id}),
        )

        self.assertEqual(response.status_code, 404)

    def test_analysis_detail_handles_missing_result(self):
        job = self.create_pr_job(dedupe_key="missing-result")

        response = self.client.get(
            reverse("dashboard:analysis_detail", kwargs={"job_id": job.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analysis result is not available yet.")

    def test_analysis_detail_ignores_malformed_json_entries(self):
        job = self.create_pr_job(dedupe_key="malformed-result")
        WorkflowAnalysisResult.objects.create(
            job=job,
            valid=False,
            findings=[
                "unexpected string",
                None,
                {
                    "severity": "medium",
                    "rule_id": "valid-entry",
                    "title": "Valid finding",
                },
            ],
            diagnostics=["unexpected diagnostic", {"code": "valid-diagnostic"}],
        )

        response = self.client.get(
            reverse("dashboard:analysis_detail", kwargs={"job_id": job.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Valid finding")
        self.assertContains(response, "valid-diagnostic")
        self.assertEqual(len(response.context["findings"]), 1)
        self.assertEqual(len(response.context["diagnostics"]), 1)
