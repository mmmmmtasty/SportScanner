from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class PlexRegistrationResult:
    provider_identifier: str
    provider_uri: str
    provider_group_id: int | None


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
            response = client.post(
                "/media/providers/metadata",
                params={**self._auth_params(), "uri": provider_uri},
            )
            if response.status_code != 409:
                response.raise_for_status()
                logger.info("plex_provider_registered provider_identifier=%s provider_uri=%s", provider_identifier, provider_uri)
            else:
                logger.info("plex_provider_conflict_reused provider_identifier=%s provider_uri=%s", provider_identifier, provider_uri)

            groups_response = client.get("/media/providers/metadata/group", params=self._auth_params())
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
                create_response = client.post(
                    "/media/providers/metadata/group",
                    params={
                        **self._auth_params(),
                        "title": provider_group_name,
                        "primaryIdentifier": provider_identifier,
                    },
                )
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
            response = client.get("/library/sections", params=self._auth_params())
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
            response = client.get(f"/library/sections/{section_id}/refresh", params={**self._auth_params(), "force": "1"})
            response.raise_for_status()
        logger.info("plex_library_refresh_triggered section_id=%s", section_id)

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
