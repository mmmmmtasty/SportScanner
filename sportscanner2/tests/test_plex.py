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


@respx.mock
def test_create_tv_shows_library_uses_current_plex_series_pair_and_provider_group() -> None:
    client = PlexPmsClient(base_url="http://plex:32400", token="abc123")
    route = respx.post("http://plex:32400/library/sections").mock(
        return_value=httpx.Response(
            200,
            json={"MediaContainer": {"Directory": [{"key": "17"}]}},
        )
    )

    section_id = client.create_tv_shows_library(
        name="Sport_Test",
        location="/sport/sportscanner2-dev",
        provider_group_id=42,
    )

    assert section_id == 17
    assert route.called
    assert route.calls[0].request.url.params["agent"] == "tv.plex.agents.series"
    assert route.calls[0].request.url.params["scanner"] == "Plex TV Series"
    assert route.calls[0].request.url.params["metadataAgentProviderGroupId"] == "42"


@respx.mock
def test_scan_section_episodes_reads_provider_guids_and_media_paths() -> None:
    client = PlexPmsClient(base_url="http://plex:32400", token="abc123")
    route = respx.get("http://plex:32400/library/sections/17/all").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<MediaContainer size="1" totalSize="1">'
                '<Video ratingKey="555" guid="tv.plex.agents.custom.sportscanner.metadata://episode/episode_seg_primary" title="Austrian Grand Prix">'
                '<Media><Part file="/library/Formula 1/Season 2025/Austrian.mkv" /></Media>'
                "</Video>"
                "</MediaContainer>"
            ),
        )
    )

    episodes = client.scan_section_episodes(17)

    assert route.called
    assert route.calls[0].request.headers["X-Plex-Container-Size"] == "200"
    assert len(episodes) == 1
    assert episodes[0].section_id == 17
    assert episodes[0].rating_key == "555"
    assert episodes[0].file_path == "/library/Formula 1/Season 2025/Austrian.mkv"


@respx.mock
def test_delete_metadata_uses_library_metadata_endpoint() -> None:
    client = PlexPmsClient(base_url="http://plex:32400", token="abc123")
    route = respx.delete("http://plex:32400/library/metadata/555").mock(
        return_value=httpx.Response(200, text="OK")
    )

    client.delete_metadata("555")

    assert route.called
    assert route.calls[0].request.url.params["X-Plex-Token"] == "abc123"
