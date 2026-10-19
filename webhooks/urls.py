from django.urls import path

from webhooks.views import github_webhook

urlpatterns = [
    path("github/", github_webhook, name="github-webhook"),
]
