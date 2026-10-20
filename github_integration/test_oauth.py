from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from github_integration.models import GitHubIdentity


@override_settings(
    GITHUB_CLIENT_ID="test-client-id",
    GITHUB_CLIENT_SECRET="test-client-secret",
)
class GitHubOAuthTests(TestCase):
    def setUp(self):
        self.start_url = reverse("github_oauth_start")
        self.callback_url = reverse("github_oauth_callback")

    def make_http_client(self, handler):
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=10.0,
            follow_redirects=False,
        )

    def test_start_redirects_to_github_with_state(self):
        response = self.client.get(self.start_url)

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response.headers["Location"])
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.netloc, "github.com")
        self.assertEqual(params["client_id"], ["test-client-id"])
        self.assertEqual(params["scope"], ["read:user"])
        self.assertIn("state", params)
        self.assertEqual(
            self.client.session["github_oauth_state"],
            params["state"][0],
        )

    def test_start_rejects_external_next_url(self):
        response = self.client.get(
            self.start_url,
            {"next": "https://attacker.example/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.session["github_oauth_next"],
            reverse("dashboard:index"),
        )

    def test_start_reports_missing_configuration(self):
        with override_settings(
            GITHUB_CLIENT_ID="",
            GITHUB_CLIENT_SECRET="",
        ):
            response = self.client.get(self.start_url, follow=False)

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )

    def test_callback_rejects_missing_or_invalid_state(self):
        response = self.client.get(
            self.callback_url,
            {"state": "forged", "code": "test-code"},
        )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertNotIn("github_oauth_state", self.client.session)

    def test_callback_rejects_provider_denial(self):
        session = self.client.session
        session["github_oauth_state"] = "valid-state"
        session.save()

        response = self.client.get(
            self.callback_url,
            {"state": "valid-state", "error": "access_denied"},
        )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )

    def test_callback_logs_in_existing_github_identity(self):
        user = get_user_model().objects.create_user(
            username="existing-flowpilot-user",
            password="local-password",
        )
        GitHubIdentity.objects.create(
            user=user,
            github_user_id=98765,
            login="old-login",
        )

        session = self.client.session
        session["github_oauth_state"] = "valid-state"
        session["github_oauth_next"] = reverse("dashboard:index")
        session.save()

        def handler(request):
            if request.url.path == "/login/oauth/access_token":
                return httpx.Response(
                    200,
                    json={"access_token": "mock-access-token"},
                )
            if request.url.path == "/user":
                self.assertEqual(
                    request.headers["Authorization"],
                    "Bearer mock-access-token",
                )
                return httpx.Response(
                    200,
                    json={
                        "id": 98765,
                        "login": "updated-login",
                        "avatar_url": ("https://avatars.githubusercontent.com/u/98765"),
                        "html_url": "https://github.com/updated-login",
                    },
                )
            return httpx.Response(404)

        mocked_client = self.make_http_client(handler)
        with patch(
            "github_integration.views.httpx.Client",
            return_value=mocked_client,
        ):
            response = self.client.get(
                self.callback_url,
                {"state": "valid-state", "code": "mock-code"},
            )

        self.assertRedirects(
            response,
            reverse("dashboard:index"),
            fetch_redirect_response=False,
        )
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

        identity = GitHubIdentity.objects.get(github_user_id=98765)
        self.assertEqual(identity.user, user)
        self.assertEqual(identity.login, "updated-login")

    def test_callback_creates_new_user_without_linking_by_username(self):
        existing_user = get_user_model().objects.create_user(
            username="same-login",
            password="unrelated-password",
        )

        session = self.client.session
        session["github_oauth_state"] = "valid-state"
        session.save()

        def handler(request):
            if request.url.path == "/login/oauth/access_token":
                return httpx.Response(
                    200,
                    json={"access_token": "mock-access-token"},
                )
            if request.url.path == "/user":
                return httpx.Response(
                    200,
                    json={"id": 87654, "login": "same-login"},
                )
            return httpx.Response(404)

        mocked_client = self.make_http_client(handler)
        with patch(
            "github_integration.views.httpx.Client",
            return_value=mocked_client,
        ):
            response = self.client.get(
                self.callback_url,
                {"state": "valid-state", "code": "mock-code"},
            )

        self.assertEqual(response.status_code, 302)
        identity = GitHubIdentity.objects.get(github_user_id=87654)
        self.assertNotEqual(identity.user_id, existing_user.pk)
        self.assertNotEqual(identity.user.username, existing_user.username)
        self.assertFalse(identity.user.has_usable_password())

    def test_callback_handles_github_http_failure(self):
        session = self.client.session
        session["github_oauth_state"] = "valid-state"
        session.save()

        def handler(request):
            return httpx.Response(502, json={"message": "upstream error"})

        mocked_client = self.make_http_client(handler)
        with patch(
            "github_integration.views.httpx.Client",
            return_value=mocked_client,
        ):
            response = self.client.get(
                self.callback_url,
                {"state": "valid-state", "code": "mock-code"},
            )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertEqual(GitHubIdentity.objects.count(), 0)

    def _run_callback_with_responses(self, token_body, profile_body=None):
        session = self.client.session
        session["github_oauth_state"] = "valid-state"
        session.save()

        def handler(request):
            if request.url.path == "/login/oauth/access_token":
                return httpx.Response(200, json=token_body)
            if request.url.path == "/user":
                return httpx.Response(200, json=profile_body)
            return httpx.Response(404)

        mocked_client = self.make_http_client(handler)
        with patch(
            "github_integration.views.httpx.Client",
            return_value=mocked_client,
        ):
            return self.client.get(
                self.callback_url,
                {"state": "valid-state", "code": "mock-code"},
            )

    def test_callback_rejects_non_object_token_response(self):
        response = self._run_callback_with_responses(
            ["unexpected", "list"],
        )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertEqual(GitHubIdentity.objects.count(), 0)

    def test_callback_rejects_oauth_error_response(self):
        response = self._run_callback_with_responses(
            {"error": "bad_verification_code"},
        )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertEqual(GitHubIdentity.objects.count(), 0)

    def test_callback_rejects_non_object_profile(self):
        response = self._run_callback_with_responses(
            {"access_token": "mock-access-token"},
            ["unexpected", "profile"],
        )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertEqual(GitHubIdentity.objects.count(), 0)

    def test_callback_rejects_invalid_github_user_id(self):
        response = self._run_callback_with_responses(
            {"access_token": "mock-access-token"},
            {"id": True, "login": "valid-login"},
        )

        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )
        self.assertEqual(GitHubIdentity.objects.count(), 0)

    def test_callback_ignores_untrusted_profile_urls(self):
        response = self._run_callback_with_responses(
            {"access_token": "mock-access-token"},
            {
                "id": 76543,
                "login": "safe-login",
                "avatar_url": "https://attacker.example/avatar.png",
                "html_url": "javascript:alert(1)",
            },
        )

        self.assertEqual(response.status_code, 302)
        identity = GitHubIdentity.objects.get(github_user_id=76543)
        self.assertEqual(identity.avatar_url, "")
        self.assertEqual(identity.html_url, "")

    def test_callback_rejects_lookalike_github_urls(self):
        response = self._run_callback_with_responses(
            {"access_token": "mock-access-token"},
            {
                "id": 76544,
                "login": "safe-login",
                "avatar_url": ("https://github.com.attacker.example/avatar.png"),
                "html_url": "https://github.com.attacker.example/safe-login",
            },
        )

        self.assertEqual(response.status_code, 302)
        identity = GitHubIdentity.objects.get(github_user_id=76544)
        self.assertEqual(identity.avatar_url, "")
        self.assertEqual(identity.html_url, "")
