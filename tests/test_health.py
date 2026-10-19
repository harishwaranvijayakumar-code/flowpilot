import pytest
from django.test import Client


@pytest.fixture
def client():
    return Client()


def test_health_endpoint(client):
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_endpoint(client):
    response = client.get("/ready/")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
