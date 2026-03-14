from __future__ import annotations

import logging
from dataclasses import dataclass
import xml.etree.ElementTree as ET

import httpx


@dataclass(slots=True)
class PlexRegistrationResult:
    provider_identifier: str
    provider_uri: str
    provider_group_id: int | None


@dataclass(slots=True)
class PlexEpisode:
    section_id: int
    rating_key: str
    guid: str | None
    file_path: str | None
    title: str | None


logger = logging.getLogger("sportscanner.plex")


class PlexPmsClient:
    def __init__(self, *, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token

    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def with_credentials(self, base_url: str | None, token: str | None) -> "PlexPmsClient":
        return PlexPmsClient(base_url=base_url or self.base_url, token=token or self.token)

    def _auth_params(self) -> dict[str, str]:
        if not self.token:
            raise ValueError("Plex PMS token is required")
        # Plex accepts the token header for many endpoints, but some metadata-provider
        # routes reliably authorize only when the token is also present as a query param.
        return {"X-Plex-Token": self.token}

    @staticmethod
    def _extract_group_id(payload: dict) -> int | None:
        container = payload.get("MediaContainer", {}) if isinstance(payload, dict) else {}
        group = container.get("MetadataAgentProviderGroup")
        if isinstance(group, list):
            if not group:
                return None
            first = group[0]
            if isinstance(first, dict) and first.get("id") is not None:
                return int(first["id"])
            return None
        if isinstance(group, dict) and group.get("id") is not None:
            return int(group["id"])
        return None

    def register_provider_and_group(
        self,
        *,
        provider_uri: str,
        provider_identifier: str,
        provider_group_name: str,
    ) -> PlexRegistrationResult:
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")

        with httpx.Client(
            base_url=self.base_url,
            headers={"X-Plex-Token": self.token, "Accept": "application/json"},
            timeout=20.0,
        ) as client:
            try:
                response = client.post(
                    "/media/providers/metadata",
                    params={**self._auth_params(), "uri": provider_uri},
                )
            except httpx.HTTPError as exc:
                logger.warning("plex_register_provider_failed provider_uri=%s error=%s", provider_uri, exc)
                raise
            if response.status_code != 409:
                response.raise_for_status()
                logger.info("plex_provider_registered provider_identifier=%s provider_uri=%s", provider_identifier, provider_uri)
            else:
                logger.info("plex_provider_conflict_reused provider_identifier=%s provider_uri=%s", provider_identifier, provider_uri)

            try:
                groups_response = client.get("/media/providers/metadata/group", params=self._auth_params())
            except httpx.HTTPError as exc:
                logger.warning("plex_group_lookup_failed provider_identifier=%s error=%s", provider_identifier, exc)
                raise
            groups_response.raise_for_status()
            container = groups_response.json().get("MediaContainer", {})
            groups = container.get("MetadataAgentProviderGroup", []) or []
            existing_group = next(
                (
                    item for item in groups
                    if item.get("title") == provider_group_name
                    or item.get("primaryIdentifier") == provider_identifier
                ),
                None,
            )
            if existing_group is not None:
                group_id = int(existing_group["id"])
                logger.info("plex_group_reused provider_group_name=%s provider_group_id=%s", provider_group_name, group_id)
            else:
                try:
                    create_response = client.post(
                        "/media/providers/metadata/group",
                        params={
                            **self._auth_params(),
                            "title": provider_group_name,
                            "primaryIdentifier": provider_identifier,
                        },
                    )
                except httpx.HTTPError as exc:
                    logger.warning("plex_group_create_failed provider_group_name=%s error=%s", provider_group_name, exc)
                    raise
                create_response.raise_for_status()
                group_id = self._extract_group_id(create_response.json())
                if group_id is None:
                    raise ValueError("Plex did not return a provider group id")
                logger.info("plex_group_created provider_group_name=%s provider_group_id=%s", provider_group_name, group_id)

        return PlexRegistrationResult(
            provider_identifier=provider_identifier,
            provider_uri=provider_uri,
            provider_group_id=group_id,
        )

    def create_tv_shows_library(
        self,
        *,
        name: str,
        location: str,
        provider_group_id: int,
        agent: str | None = None,
    ) -> int:
        """Create a TV Shows library in Plex and return the new section id."""
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")
        with httpx.Client(
            base_url=self.base_url,
            headers={"X-Plex-Token": self.token, "Accept": "application/json"},
            timeout=20.0,
        ) as client:
            try:
                response = client.post(
                    "/library/sections",
                    params={
                        "name": name,
                        "type": "show",
                        "agent": agent or "tv.plex.agents.series",
                        "scanner": "Plex TV Series",
                        "language": "en-US",
                        "location": location,
                        "metadataAgentProviderGroupId": provider_group_id,
                        "flattenSeasons": "0",
                        "X-Plex-Token": self.token,
                    },
                )
            except httpx.HTTPError as exc:
                logger.warning("plex_create_library_failed name=%s location=%s error=%s", name, location, exc)
                raise
            response.raise_for_status()
            directories = response.json().get("MediaContainer", {}).get("Directory", [])
            if isinstance(directories, dict):
                directories = [directories]
            if not directories:
                raise ValueError("Plex did not return a library section after creation")
            section_id = int(directories[0]["key"])
            logger.info(
                "plex_library_created name=%s location=%s section_id=%s",
                name,
                location,
                section_id,
            )
            return section_id

    def list_library_sections(self) -> list[dict]:
        """Return all library sections from Plex as a list of dicts."""
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")
        with httpx.Client(base_url=self.base_url, headers={"X-Plex-Token": self.token, "Accept": "application/json"}, timeout=20.0) as client:
            try:
                response = client.get("/library/sections", params=self._auth_params())
            except httpx.HTTPError as exc:
                logger.warning("plex_list_sections_failed error=%s", exc)
                raise
            response.raise_for_status()
        directories = response.json().get("MediaContainer", {}).get("Directory", [])
        if isinstance(directories, dict):
            directories = [directories]
        return [
            {
                "key": int(d["key"]),
                "title": d.get("title", ""),
                "type": d.get("type", ""),
                "agent": d.get("agent", ""),
                "scanner": d.get("scanner", ""),
            }
            for d in directories
        ]

    def refresh_library_section(self, section_id: int) -> None:
        """Trigger a forced metadata refresh on a Plex library section."""
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")
        with httpx.Client(base_url=self.base_url, headers={"X-Plex-Token": self.token}, timeout=20.0) as client:
            try:
                response = client.get(
                    f"/library/sections/{section_id}/refresh",
                    params={**self._auth_params(), "force": "1"},
                )
            except httpx.HTTPError as exc:
                logger.warning("plex_section_refresh_failed section_id=%s error=%s", section_id, exc)
                raise
            response.raise_for_status()
        logger.info("plex_library_refresh_triggered section_id=%s", section_id)

    def find_show_section_ids(self) -> list[int]:
        """Return the IDs of all Plex library sections with type 'show'."""
        try:
            sections = self.list_library_sections()
        except Exception:
            return []
        return [s["key"] for s in sections if s.get("type") == "show"]

    def scan_section_episodes(self, section_id: int, *, page_size: int = 200) -> list[PlexEpisode]:
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")

        episodes: list[PlexEpisode] = []
        start = 0
        with httpx.Client(
            base_url=self.base_url,
            headers={"X-Plex-Token": self.token, "Accept": "application/xml"},
            timeout=30.0,
        ) as client:
            while True:
                try:
                    response = client.get(
                        f"/library/sections/{section_id}/all",
                        params={**self._auth_params(), "type": "4", "includeGuids": "1"},
                        headers={
                            "X-Plex-Container-Start": str(start),
                            "X-Plex-Container-Size": str(page_size),
                        },
                    )
                except httpx.HTTPError as exc:
                    logger.warning("plex_scan_section_failed section_id=%s error=%s", section_id, exc)
                    raise
                response.raise_for_status()

                root = ET.fromstring(response.text)
                videos = root.findall(".//Video")
                for video in videos:
                    part = video.find(".//Part")
                    episodes.append(
                        PlexEpisode(
                            section_id=section_id,
                            rating_key=video.attrib["ratingKey"],
                            guid=video.attrib.get("guid"),
                            file_path=part.attrib.get("file") if part is not None else None,
                            title=video.attrib.get("title"),
                        )
                    )

                returned = len(videos)
                total_size = int(root.attrib.get("totalSize") or 0)
                if returned == 0:
                    break
                start += returned
                if total_size and start >= total_size:
                    break
                if returned < page_size and not total_size:
                    break

        return episodes

    def scan_show_section_episodes(self, *, page_size: int = 200) -> list[PlexEpisode]:
        episodes: list[PlexEpisode] = []
        for section_id in self.find_show_section_ids():
            episodes.extend(self.scan_section_episodes(section_id, page_size=page_size))
        return episodes

    def delete_metadata(self, rating_key: str) -> None:
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")
        with httpx.Client(base_url=self.base_url, headers={"X-Plex-Token": self.token}, timeout=20.0) as client:
            try:
                response = client.delete(
                    f"/library/metadata/{rating_key}",
                    params=self._auth_params(),
                )
            except httpx.HTTPError as exc:
                logger.warning("plex_metadata_delete_failed rating_key=%s error=%s", rating_key, exc)
                raise
            response.raise_for_status()
        logger.info("plex_metadata_deleted rating_key=%s", rating_key)

    def library_uses_group(self, section_id: int, group_id: int) -> bool:
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")
        with httpx.Client(base_url=self.base_url, headers={"X-Plex-Token": self.token}, timeout=20.0) as client:
            response = client.get(f"/library/sections/{section_id}", params=self._auth_params())
            response.raise_for_status()
            directory = response.json().get("MediaContainer", {}).get("Directory", [])
            if not directory:
                return False
            return int(directory[0].get("metadataAgentProviderGroupId") or 0) == group_id
