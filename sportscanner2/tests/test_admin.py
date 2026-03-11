from __future__ import annotations

import logging

import httpx
from fastapi.testclient import TestClient

from sportscanner.log_buffer import LogBuffer
from sportscanner.plex import PlexRegistrationResult


def test_settings_page_explains_plex_fields(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/settings")

    assert response.status_code == 200
    assert "Plex Server URL" in response.text
    assert "Plex Token (X-Plex-Token)" in response.text
    assert "Provider Identifier In Plex" in response.text
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
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/settings")


def test_save_settings_rejects_invalid_provider_identifier(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.post(
        "/admin/settings",
        data={
            "pms_url": "http://plex:32400",
            "pms_token": "abc123",
            "provider_public_url": "http://sportscanner:32699",
            "plex_provider_identifier": "sportscanner.metadata",
            "plex_provider_group_name": "SportScanner 2",
        },
    )

    assert response.status_code == 400
    assert "Action failed" in response.text
    assert "must start with tv.plex.agents." in response.text


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
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/admin/register-plex?")


def test_register_plex_page_renders_create_library_form(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 200
    assert "Create Plex Test Library" in response.text


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
            "plex_provider_identifier": "tv.plex.agents.custom.sportscanner.metadata.local",
            "plex_provider_group_name": "SportScanner 2",
        },
        follow_redirects=False,
    )

    response = client.post("/admin/register-plex", follow_redirects=False)

    assert response.status_code == 200
    assert "Action failed" in response.text
    assert "Plex returned 400 Bad Request." in response.text


def test_stats_json_returns_counts(provider_app) -> None:
    client = TestClient(provider_app)

    response = client.get("/admin/stats.json")

    assert response.status_code == 200
    data = response.json()
    assert "competitions" in data
    assert "published_segments" in data
    assert "open_review_tasks" in data


def test_logs_page_renders(provider_app) -> None:
    buf = LogBuffer(session_factory=provider_app.state.services.session_factory)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sportscanner.test_logs_page")
    logger.setLevel(logging.INFO)
    logger.addHandler(buf)
    provider_app.state.log_buffer = buf
    try:
        logger.info("persisted log entry")
        client = TestClient(provider_app)

        response = client.get("/admin/logs")

        assert response.status_code == 200
        assert "Logs" in response.text
        assert "persisted log entry" in response.text
    finally:
        logger.removeHandler(buf)


def test_logs_entries_returns_filtered_rows(provider_app) -> None:
    buf = LogBuffer(session_factory=provider_app.state.services.session_factory)
    buf.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("sportscanner.test_logs")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buf)
    provider_app.state.log_buffer = buf
    try:
        logger.info("hello from info")
        logger.debug("hello from debug")
        logger.error("something went wrong")

        client = TestClient(provider_app)

        response = client.get("/admin/logs/entries")
        assert response.status_code == 200
        assert "hello from info" in response.text

        response = client.get("/admin/logs/entries?level=ERROR")
        assert "something went wrong" in response.text
        assert "hello from info" not in response.text

        response = client.get("/admin/logs/entries?keyword=wrong")
        assert "something went wrong" in response.text
        assert "hello from info" not in response.text
    finally:
        logger.removeHandler(buf)
