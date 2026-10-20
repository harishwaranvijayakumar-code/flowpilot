from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.db import connection
from django.db.utils import DatabaseError
from django.http import JsonResponse
from django.urls import include, path

from github_integration import views as github_views


def health(request):
    return JsonResponse({"status": "ok"})


def readiness(request):
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse(
            {"status": "not_ready", "reason": "database_unavailable"},
            status=503,
        )

    return JsonResponse({"status": "ready"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("ready/", readiness, name="readiness"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
        ),
        name="login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(
            template_name="registration/flowpilot_logged_out.html",
        ),
        name="logout",
    ),
    path("webhooks/", include("webhooks.urls")),
    path(
        "accounts/github/",
        github_views.github_oauth_start,
        name="github_oauth_start",
    ),
    path(
        "accounts/github/callback/",
        github_views.github_oauth_callback,
        name="github_oauth_callback",
    ),
    path("", include("dashboard.urls")),
]
