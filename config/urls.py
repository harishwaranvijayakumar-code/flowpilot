from django.db import connection
from django.db.utils import DatabaseError
from django.http import JsonResponse
from django.urls import path


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
    path("health/", health, name="health"),
    path("ready/", readiness, name="readiness"),
]
