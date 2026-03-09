from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from sportscanner.plex import PlexRegistrationResult


def test_settings_page_explains_plex_fields(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/settings")

    assert response.status_code == 200
    assert "Plex Server URL" in response.text
    assert "Plex Token (X-Plex-Token)" in response.text
    assert "Save Settings" in response.text
    assert "Register Provider And Group" in response.text


def test_save_settings_redirects_back_to_settings(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/settings")


def test_register_plex_redirects_to_get_result_page(provider_app) -> None:
    class FakePlex:
        def with_credentials(self, base_url, token):
            return self

        def register_provider_and_group(self, *, provider_uri, provider_identifier, provider_group_name):
            return PlexRegistrationResult(
                provider_identifier=provider_identifier,
                provider_uri=provider_uri,
                provider_group_id=42,
            )

    provider_app.state.services.plex = FakePlex()
    client = TestClient(provider_app)

    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/admin/register-plex?")


def test_register_plex_result_page_requires_query_values(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/settings")


def test_register_plex_failure_renders_html_error(provider_app) -> None:
    class FakePlex:
        def with_credentials(self, base_url, token):
            return self

        def register_provider_and_group(self, *, provider_uri, provider_identifier, provider_group_name):
            request = httpx.Request("GET", "http://plex:32400/media/providers/metadata/group")
            response = httpx.Response(400, request=request, text="Bad Request")
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    provider_app.state.services.plex = FakePlex()
    client = TestClient(provider_app)

    client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 200
    assert "Registration failed" in response.text
    assert "Plex returned 400 Bad Request." in response.text
