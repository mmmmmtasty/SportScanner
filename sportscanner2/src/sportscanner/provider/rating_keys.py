from __future__ import annotations

DEFAULT_GUID_PREFIX = "tv.plex.agents.custom.sportscanner.metadata"


def make_show_rating_key(competition_id: str) -> str:
    return f"show_{competition_id}"


def make_season_rating_key(competition_id: str, season_number: int) -> str:
    return f"season_{competition_id}_{season_number}"


def make_episode_rating_key(segment_id: str) -> str:
    return f"episode_{segment_id}"


def make_show_guid(competition_id: str, guid_prefix: str = DEFAULT_GUID_PREFIX) -> str:
    return f"{guid_prefix}://show/{make_show_rating_key(competition_id)}"


def make_season_guid(
    competition_id: str,
    season_number: int,
    guid_prefix: str = DEFAULT_GUID_PREFIX,
) -> str:
    return f"{guid_prefix}://season/{make_season_rating_key(competition_id, season_number)}"


def make_episode_guid(segment_id: str, guid_prefix: str = DEFAULT_GUID_PREFIX) -> str:
    return f"{guid_prefix}://episode/{make_episode_rating_key(segment_id)}"


def parse_rating_key(rating_key: str) -> tuple[str, tuple[str, ...]]:
    if rating_key.startswith("show_"):
        return ("show", (rating_key.removeprefix("show_"),))
    if rating_key.startswith("season_"):
        payload = rating_key.removeprefix("season_")
        competition_id, season_number = payload.rsplit("_", 1)
        return ("season", (competition_id, season_number))
    if rating_key.startswith("episode_"):
        return ("episode", (rating_key.removeprefix("episode_"),))
    raise ValueError(f"Unsupported rating key: {rating_key}")


def parse_guid(guid: str, guid_prefix: str = DEFAULT_GUID_PREFIX) -> tuple[str, tuple[str, ...]]:
    prefix = f"{guid_prefix}://"
    if not guid.startswith(prefix):
        raise ValueError(f"Unsupported guid: {guid}")
    entity_type, rating_key = guid[len(prefix):].split("/", 1)
    parsed_type, values = parse_rating_key(rating_key)
    if parsed_type != entity_type:
        raise ValueError(f"GUID type mismatch: {guid}")
    return (entity_type, values)
