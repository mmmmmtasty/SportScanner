from __future__ import annotations

from fastapi.testclient import TestClient


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
