import secrets
from urllib.parse import urlencode, urlparse

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from github_integration.models import GitHubIdentity

OAUTH_STATE_SESSION_KEY = "github_oauth_state"
OAUTH_NEXT_SESSION_KEY = "github_oauth_next"
OAUTH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _safe_next_url(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("dashboard:index")


def _login_redirect(request, message):
    messages.error(request, message)
    return redirect("login")


def _safe_github_url(value):
    if not isinstance(value, str) or len(value) > 500:
        return ""

    parsed = urlparse(value)
    allowed_hosts = {
        "github.com",
        "avatars.githubusercontent.com",
    }

    try:
        port = parsed.port
    except ValueError:
        return ""

    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return ""

    return value


@require_GET
def github_oauth_start(request):
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        return _login_redirect(
            request,
            "GitHub sign-in is not configured yet. Use username and password instead.",
        )

    state = secrets.token_urlsafe(32)
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[OAUTH_NEXT_SESSION_KEY] = _safe_next_url(
        request,
        request.GET.get("next"),
    )

    callback_url = request.build_absolute_uri(reverse("github_oauth_callback"))
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": callback_url,
        "scope": "read:user",
        "state": state,
    }

    return redirect(f"{settings.GITHUB_OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


@require_GET
def github_oauth_callback(request):
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
    next_url = request.session.pop(
        OAUTH_NEXT_SESSION_KEY,
        reverse("dashboard:index"),
    )
    returned_state = request.GET.get("state", "")

    if (
        not expected_state
        or not returned_state
        or not constant_time_compare(expected_state, returned_state)
    ):
        return _login_redirect(
            request,
            "GitHub sign-in could not be verified. Please try again.",
        )

    if request.GET.get("error"):
        return _login_redirect(
            request,
            "GitHub sign-in was cancelled or denied.",
        )

    code = request.GET.get("code", "")
    if not code or len(code) > 2048:
        return _login_redirect(
            request,
            "GitHub did not return a valid authorization code.",
        )

    callback_url = request.build_absolute_uri(reverse("github_oauth_callback"))

    try:
        with httpx.Client(
            timeout=OAUTH_TIMEOUT,
            follow_redirects=False,
        ) as client:
            token_response = client.post(
                settings.GITHUB_OAUTH_TOKEN_URL,
                data={
                    "client_id": settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": callback_url,
                },
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            token_data = token_response.json()

            if not isinstance(token_data, dict):
                return _login_redirect(
                    request,
                    "GitHub returned an invalid sign-in response.",
                )

            if token_data.get("error"):
                return _login_redirect(
                    request,
                    "GitHub could not authorize sign-in. Please try again.",
                )

            access_token = token_data.get("access_token")
            if (
                not isinstance(access_token, str)
                or not access_token.strip()
                or len(access_token) > 8192
            ):
                return _login_redirect(
                    request,
                    "GitHub sign-in failed. Please try again.",
                )

            user_response = client.get(
                settings.GITHUB_OAUTH_USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            user_response.raise_for_status()
            github_data = user_response.json()

            if not isinstance(github_data, dict):
                return _login_redirect(
                    request,
                    "GitHub returned an invalid account profile.",
                )

    except (httpx.HTTPError, ValueError):
        return _login_redirect(
            request,
            "GitHub could not complete sign-in. Please try again.",
        )

    github_user_id = github_data.get("id")
    github_login = github_data.get("login")

    if (
        isinstance(github_user_id, bool)
        or not isinstance(github_user_id, int)
        or github_user_id <= 0
        or not isinstance(github_login, str)
        or not github_login.strip()
        or len(github_login) > 255
    ):
        return _login_redirect(
            request,
            "GitHub returned an invalid account profile.",
        )

    avatar_url = _safe_github_url(github_data.get("avatar_url"))
    html_url = _safe_github_url(github_data.get("html_url"))

    try:
        identity = GitHubIdentity.objects.select_related("user").get(
            github_user_id=github_user_id
        )
        identity.login = github_login
        identity.avatar_url = avatar_url
        identity.html_url = html_url
        identity.save(
            update_fields=[
                "login",
                "avatar_url",
                "html_url",
                "updated_at",
            ]
        )
        user = identity.user

    except GitHubIdentity.DoesNotExist:
        try:
            user = _create_github_user(
                github_user_id=github_user_id,
                github_login=github_login,
                avatar_url=avatar_url,
                html_url=html_url,
            )
        except IntegrityError:
            # A concurrent callback may have created this identity.
            identity = (
                GitHubIdentity.objects.filter(github_user_id=github_user_id)
                .select_related("user")
                .first()
            )
            if identity is None:
                return _login_redirect(
                    request,
                    "Your GitHub account could not be linked. Please try again.",
                )
            user = identity.user

    login(
        request,
        user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    return redirect(_safe_next_url(request, next_url))


def _create_github_user(
    *,
    github_user_id,
    github_login,
    avatar_url,
    html_url,
):
    user_model = get_user_model()
    base_username = (
        "".join(
            character
            for character in github_login.lower()
            if character.isalnum() or character in "-_"
        )[:120].strip("-_")
        or "github-user"
    )

    with transaction.atomic():
        username = base_username
        suffix = 0

        while user_model.objects.filter(username=username).exists():
            suffix += 1
            username = f"{base_username[:130]}-{github_user_id}-{suffix}"

        user = user_model.objects.create_user(
            username=username,
            password=None,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        GitHubIdentity.objects.create(
            user=user,
            github_user_id=github_user_id,
            login=github_login,
            avatar_url=avatar_url,
            html_url=html_url,
        )

        return user
