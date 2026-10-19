import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from github_integration.client import (
    GitHubAppClient,
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubTransientError,
)


@pytest.fixture
def private_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")


def make_client(private_key, handler):
    http_client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    return GitHubAppClient(
        app_id="12345",
        private_key=private_key,
        http_client=http_client,
    )


def test_app_jwt_is_signed_and_short_lived(private_key):
    client = GitHubAppClient("12345", private_key)
    try:
        token = client.create_app_jwt()
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_iat": False,
            },
            algorithms=["RS256"],
        )
        assert claims["iss"] == "12345"
        assert claims["exp"] - claims["iat"] == 600
        assert claims["iat"] <= int(time.time())
    finally:
        client.close()


def test_installation_token_exchange(private_key):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/app/installations/77/access_tokens"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        return httpx.Response(201, json={"token": "temporary-test-token"})

    client = make_client(private_key, handler)
    try:
        assert client.create_installation_token(77) == "temporary-test-token"
    finally:
        client.close()


def test_authenticated_api_request(private_key):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/app/installations/77/access_tokens":
            return httpx.Response(201, json={"token": "temporary-test-token"})
        assert request.url.path == "/repos/example/project"
        assert request.headers["Authorization"] == "Bearer temporary-test-token"
        return httpx.Response(200, json={"full_name": "example/project"})

    client = make_client(private_key, handler)
    try:
        result = client.request(
            "GET",
            "/repos/example/project",
            installation_id=77,
        )
        assert result == {"full_name": "example/project"}
        assert len(calls) == 2
    finally:
        client.close()


@pytest.mark.parametrize(
    ("status", "headers", "error_type"),
    [
        (401, {}, GitHubAuthenticationError),
        (403, {}, GitHubPermissionError),
        (403, {"X-RateLimit-Remaining": "0"}, GitHubRateLimitError),
        (429, {}, GitHubTransientError),
        (503, {}, GitHubTransientError),
        (422, {}, GitHubResponseError),
    ],
)
def test_github_http_errors(private_key, status, headers, error_type):
    def handler(request):
        return httpx.Response(status, headers=headers, json={"message": "test"})

    client = make_client(private_key, handler)
    try:
        with pytest.raises(error_type):
            client.create_installation_token(77)
    finally:
        client.close()


def test_invalid_installation_id_is_rejected(private_key):
    client = GitHubAppClient("12345", private_key)
    try:
        with pytest.raises(GitHubConfigurationError):
            client.create_installation_token(0)
    finally:
        client.close()


def test_invalid_private_key_is_reported():
    client = GitHubAppClient("12345", "not-a-private-key")
    try:
        with pytest.raises(GitHubConfigurationError):
            client.create_app_jwt()
    finally:
        client.close()
