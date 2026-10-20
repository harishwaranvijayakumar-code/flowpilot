from django.urls import path

from dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("pull-requests/", views.pull_requests, name="pull_requests"),
    path(
        "pull-requests/<uuid:job_id>/",
        views.analysis_detail,
        name="analysis_detail",
    ),
    path(
        "repositories/access/",
        views.repository_access,
        name="repository_access",
    ),
]
