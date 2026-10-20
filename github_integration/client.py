import time
from typing import Any

import httpx
import jwt
from django.conf import settings

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 10.0
GITHUB_API_VERSION = "2022-11-28"


class GitHubAPIError(Exception):
    """Base exception for GitHub API failures."""


class GitHubConfigurationError(GitHubAPIError):
    """GitHub App configuration is missing or invalid."""


class GitHubAuthenticationError(GitHubAPIError):
    """GitHub rejected the App JWT or installation token."""


class GitHubPermissionError(GitHubAPIError):
    """The installation does not have the required permission."""


class GitHubNotFoundError(GitHubPermissionError):
    """GitHub could not find the requested resource."""


class GitHubRateLimitError(GitHubAPIError):
    """GitHub rate-limited the request."""


class GitHubTransientError(GitHubAPIError):
    """A temporary GitHub or network failure occurred."""


class GitHubResponseError(GitHubAPIError):
    """GitHub returned an unexpected or unsuccessful response."""


class GitHubAppClient:
    """Minimal GitHub App client with explicit timeouts and safe errors."""

    def __init__(
        self,
        app_id: str | int,
        private_key: str,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not str(app_id).strip():
            raise GitHubConfigurationError("GitHub App ID is required.")
        if not private_key.strip():
            raise GitHubConfigurationError("GitHub App private key is required.")
        if timeout <= 0:
            raise GitHubConfigurationError("HTTP timeout must be positive.")

        self.app_id = str(app_id).strip()
        self.private_key = private_key.replace("\\n", "\n")
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=self.api_url,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )

    @classmethod
    def from_settings(cls) -> "GitHubAppClient":
        """Construct the client from Django environment-backed settings."""
        return cls(
            app_id=getattr(settings, "GITHUB_APP_ID", ""),
            private_key=getattr(settings, "GITHUB_APP_PRIVATE_KEY", ""),
            api_url=getattr(settings, "GITHUB_API_URL", DEFAULT_API_URL),
        )

    def create_app_jwt(self) -> str:
        """Create a short-lived RS256 JWT for GitHub App authentication."""
        now = int(time.time())
        claims = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.app_id,
        }

        try:
            return jwt.encode(
                claims,
                self.private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise GitHubConfigurationError(
                "Could not sign the GitHub App JWT; check the private key."
            ) from exc

    def get_installation(self, installation_id: int) -> dict[str, Any]:
        """
        Fetch the current installation state using the GitHub App JWT.

        This endpoint is intentionally authenticated with the App JWT,
        not an installation access token.
        """
        if (
            isinstance(installation_id, bool)
            or not isinstance(installation_id, int)
            or installation_id <= 0
        ):
            raise GitHubConfigurationError(
                "Installation ID must be a positive integer."
            )

        response = self._send(
            "GET",
            f"/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {self.create_app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )
        self._raise_for_status(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise GitHubResponseError(
                "GitHub returned an invalid installation response."
            ) from exc

        if not isinstance(data, dict):
            raise GitHubResponseError(
                "GitHub returned an invalid installation response."
            )

        returned_id = data.get("id")
        if (
            isinstance(returned_id, bool)
            or not isinstance(returned_id, int)
            or returned_id != installation_id
        ):
            raise GitHubResponseError("GitHub returned an unexpected installation ID.")

        return data

    def create_installation_token(self, installation_id: int) -> str:
        """Exchange the App JWT for a short-lived installation token."""
        if (
            isinstance(installation_id, bool)
            or not isinstance(installation_id, int)
            or installation_id <= 0
        ):
            raise GitHubConfigurationError(
                "Installation ID must be a positive integer."
            )

        response = self._send(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.create_app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )
        self._raise_for_status(response)

        try:
            data = response.json()
            token = data["token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubResponseError(
                "GitHub returned an invalid installation-token response."
            ) from exc

        if not isinstance(token, str) or not token:
            raise GitHubResponseError("GitHub returned an empty installation token.")

        return token

    def list_installation_repositories(
        self,
        installation_id: int,
    ) -> list[dict[str, Any]]:
        """List every repository accessible to an installation."""
        if (
            isinstance(installation_id, bool)
            or not isinstance(installation_id, int)
            or installation_id <= 0
        ):
            raise GitHubConfigurationError(
                "Installation ID must be a positive integer."
            )

        repositories = []
        page = 1
        total_count = None

        while total_count is None or len(repositories) < total_count:
            data = self.request(
                "GET",
                "/installation/repositories",
                installation_id=installation_id,
                params={"per_page": 100, "page": page},
            )

            if not isinstance(data, dict):
                raise GitHubResponseError(
                    "GitHub returned an invalid repository-list response."
                )

            page_repositories = data.get("repositories")
            page_total_count = data.get("total_count")

            if (
                isinstance(page_total_count, bool)
                or not isinstance(page_total_count, int)
                or page_total_count < 0
                or not isinstance(page_repositories, list)
            ):
                raise GitHubResponseError(
                    "GitHub returned an invalid repository-list response."
                )

            if total_count is None:
                total_count = page_total_count
            elif page_total_count != total_count:
                raise GitHubResponseError(
                    "GitHub repository count changed during pagination."
                )

            if not page_repositories and len(repositories) < total_count:
                raise GitHubResponseError(
                    "GitHub returned an incomplete repository list."
                )

            for repository in page_repositories:
                if not isinstance(repository, dict):
                    raise GitHubResponseError(
                        "GitHub returned an invalid repository entry."
                    )

                repository_id = repository.get("id")
                full_name = repository.get("full_name")
                html_url = repository.get("html_url")

                if (
                    isinstance(repository_id, bool)
                    or not isinstance(repository_id, int)
                    or repository_id <= 0
                    or not isinstance(full_name, str)
                    or not full_name.strip()
                    or len(full_name) > 255
                    or not isinstance(html_url, str)
                    or not html_url.startswith("https://")
                ):
                    raise GitHubResponseError(
                        "GitHub returned an invalid repository entry."
                    )

                repositories.append(repository)

            if len(repositories) > total_count:
                raise GitHubResponseError(
                    "GitHub returned more repositories than reported."
                )

            page += 1

        return repositories

    def request(
        self,
        method: str,
        path: str,
        *,
        installation_id: int,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated request using an installation token."""
        token = self.create_installation_token(installation_id)
        response = self._send(
            method,
            path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            params=params,
            json=json_body,
        )
        self._raise_for_status(response)

        if response.status_code == 204:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise GitHubResponseError(
                "GitHub returned a response that was not valid JSON."
            ) from exc

    def _send(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request without following redirects or exposing secrets."""
        if not path.startswith("/") or path.startswith("//"):
            raise GitHubConfigurationError(
                "GitHub API paths must be absolute paths, not URLs."
            )

        try:
            return self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise GitHubTransientError("GitHub API request timed out.") from exc
        except httpx.TransportError as exc:
            raise GitHubTransientError("GitHub API transport failed.") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Translate GitHub status codes without including response secrets."""
        status = response.status_code

        if status == 401:
            raise GitHubAuthenticationError(
                "GitHub rejected the authentication credentials."
            )

        if status == 403 and (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "Retry-After" in response.headers
        ):
            raise GitHubRateLimitError("GitHub rate limit reached.")

        if status == 404:
            raise GitHubNotFoundError("GitHub could not find the requested resource.")

        if status == 403:
            raise GitHubPermissionError(
                "GitHub denied access to the requested resource."
            )

        if status in {408, 425, 429} or status >= 500:
            raise GitHubTransientError(
                f"GitHub returned a temporary HTTP {status} response."
            )

        if not 200 <= status < 300:
            raise GitHubResponseError(
                f"GitHub returned an unexpected HTTP {status} response."
            )

    def close(self) -> None:
        """Close the HTTP client if this instance created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "GitHubAppClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
