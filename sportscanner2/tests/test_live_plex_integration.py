from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import httpx
import pytest


pytestmark = pytest.mark.plex_integration


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    pytest.skip(f"{name} is required for live Plex integration tests")


def test_live_plex_registration_and_library() -> None:
    provider_base_url = os.getenv("SPORTSCANNER_PROVIDER_URL", "http://127.0.0.1:32699").rstrip("/")
    plex_base_url = os.getenv("SPORTSCANNER_PMS_URL", "http://192.168.0.127:32400").rstrip("/")
    plex_token = _required_env("SPORTSCANNER_PMS_TOKEN")
    provider_identifier = os.getenv(
        "SPORTSCANNER_PROVIDER_IDENTIFIER",
        "tv.plex.agents.custom.sportscanner.metadata.local",
    )
    provider_group_name = os.getenv("SPORTSCANNER_PROVIDER_GROUP_NAME", "SportScanner 2 Local")
    library_name = os.getenv("SPORTSCANNER_PLEX_LIBRARY_NAME", "Sport_Test")

    with httpx.Client(timeout=20.0, headers={"X-Plex-Token": plex_token}) as client:
        provider_health = client.get(f"{provider_base_url}/health")
        assert provider_health.status_code == 200

        provider_root = client.get(f"{provider_base_url}/provider/tv")
        assert provider_root.status_code == 200
        provider_payload = provider_root.json()["MediaProvider"]
        assert provider_payload["identifier"] == provider_identifier
        assert provider_payload["title"] == provider_group_name

        groups_response = client.get(
            f"{plex_base_url}/media/providers/metadata/group",
            headers={"Accept": "application/json"},
        )
        assert groups_response.status_code == 200
        groups = groups_response.json()["MediaContainer"].get("MetadataAgentProviderGroup", [])
        if isinstance(groups, dict):
            groups = [groups]
        assert any(
            group.get("primaryIdentifier") == provider_identifier and group.get("title") == provider_group_name
            for group in groups
        )

        libraries_response = client.get(f"{plex_base_url}/library/sections")
        assert libraries_response.status_code == 200
        root = ET.fromstring(libraries_response.text)
        directories = root.findall(".//Directory")
        assert any(
            directory.attrib.get("title") == library_name and directory.attrib.get("type") == "show"
            for directory in directories
        )
