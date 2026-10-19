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
