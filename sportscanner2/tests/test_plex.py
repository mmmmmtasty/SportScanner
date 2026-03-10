from __future__ import annotations

import httpx
import respx

from sportscanner.plex import PlexPmsClient


@respx.mock
def test_register_provider_and_group_uses_current_plex_group_endpoints() -> None:
    provider_uri = "http://sportscanner:32699/provider/tv"
    client = PlexPmsClient(base_url="http://plex:32400", token="abc123")

    register_route = respx.post("http://plex:32400/media/providers/metadata").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {}})
    )
    list_groups_route = respx.get("http://plex:32400/media/providers/metadata/group").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"MetadataAgentProviderGroup": []}},
        )
    )
    create_group_route = respx.post("http://plex:32400/media/providers/metadata/group").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"MetadataAgentProviderGroup": [{"id": 42}]}},
        )
    )

    result = client.register_provider_and_group(
        provider_uri=provider_uri,
        provider_identifier="tv.plex.agents.custom.sportscanner.metadata",
        provider_group_name="SportScanner 2",
    )

    assert register_route.called
    assert register_route.calls[0].request.url.params["uri"] == provider_uri
    assert register_route.calls[0].request.url.params["X-Plex-Token"] == "abc123"
    assert list_groups_route.called
    assert list_groups_route.calls[0].request.url.params["X-Plex-Token"] == "abc123"
    assert create_group_route.called
    assert create_group_route.calls[0].request.url.params["X-Plex-Token"] == "abc123"
    assert create_group_route.calls[0].request.url.params["title"] == "SportScanner 2"
    assert (
        create_group_route.calls[0].request.url.params["primaryIdentifier"]
        == "tv.plex.agents.custom.sportscanner.metadata"
    )
    assert result.provider_group_id == 42


@respx.mock
def test_register_provider_and_group_reuses_existing_group() -> None:
    client = PlexPmsClient(base_url="http://plex:32400", token="abc123")

    respx.post("http://plex:32400/media/providers/metadata").mock(
        return_value=httpx.Response(200, json={"MediaContainer": {}})
    )
    respx.get("http://plex:32400/media/providers/metadata/group").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "MetadataAgentProviderGroup": [
                        {
                            "id": 99,
                            "title": "SportScanner 2",
                            "primaryIdentifier": "tv.plex.agents.custom.sportscanner.metadata",
                        }
                    ]
                }
            },
        )
    )
    create_group_route = respx.post("http://plex:32400/media/providers/metadata/group").mock(
        return_value=httpx.Response(500)
    )

    result = client.register_provider_and_group(
        provider_uri="http://sportscanner:32699/provider/tv",
        provider_identifier="tv.plex.agents.custom.sportscanner.metadata",
        provider_group_name="SportScanner 2",
    )

    assert not create_group_route.called
    assert result.provider_group_id == 99


@respx.mock
def test_register_provider_and_group_accepts_existing_provider_conflict() -> None:
    client = PlexPmsClient(base_url="http://plex:32400", token="abc123")

    respx.post("http://plex:32400/media/providers/metadata").mock(
        return_value=httpx.Response(409, text="A provider with the same identifier already exists.")
    )
    respx.get("http://plex:32400/media/providers/metadata/group").mock(
        return_value=httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "MetadataAgentProviderGroup": [
                        {
                            "id": 8,
                            "title": "SportScanner 2 Local",
                            "primaryIdentifier": "tv.plex.agents.custom.sportscanner.metadata.local",
                        }
                    ]
                }
            },
        )
    )

    result = client.register_provider_and_group(
        provider_uri="http://sportscanner:32699/provider/tv",
        provider_identifier="tv.plex.agents.custom.sportscanner.metadata.local",
        provider_group_name="SportScanner 2 Local",
    )

    assert result.provider_group_id == 8


def test_extract_group_id_accepts_object_or_list() -> None:
    assert PlexPmsClient._extract_group_id({"MediaContainer": {"MetadataAgentProviderGroup": {"id": 7}}}) == 7
    assert PlexPmsClient._extract_group_id({"MediaContainer": {"MetadataAgentProviderGroup": [{"id": 8}]}}) == 8
    assert PlexPmsClient._extract_group_id({"MediaContainer": {"MetadataAgentProviderGroup": []}}) is None
