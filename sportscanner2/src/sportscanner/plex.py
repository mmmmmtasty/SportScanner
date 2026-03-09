from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class PlexRegistrationResult:
    provider_identifier: str
    provider_uri: str
    provider_group_id: int | None


class PlexPmsClient:
    def __init__(self, *, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token

    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def with_credentials(self, base_url: str | None, token: str | None) -> "PlexPmsClient":
        return PlexPmsClient(base_url=base_url or self.base_url, token=token or self.token)

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
            response = client.post("/media/providers/metadata", params={"uri": provider_uri})
            response.raise_for_status()

            groups_response = client.get("/media/providers/metadata/group")
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
            else:
                create_response = client.post(
                    "/media/providers/metadata/group",
                    params={"title": provider_group_name, "primaryIdentifier": provider_identifier},
                )
                create_response.raise_for_status()
                group_id = self._extract_group_id(create_response.json())
                if group_id is None:
                    raise ValueError("Plex did not return a provider group id")

        return PlexRegistrationResult(
            provider_identifier=provider_identifier,
            provider_uri=provider_uri,
            provider_group_id=group_id,
        )

    def library_uses_group(self, section_id: int, group_id: int) -> bool:
        if not self.configured():
            raise ValueError("Plex PMS URL and token are required")
        with httpx.Client(base_url=self.base_url, headers={"X-Plex-Token": self.token}, timeout=20.0) as client:
            response = client.get(f"/library/sections/{section_id}")
            response.raise_for_status()
            directory = response.json().get("MediaContainer", {}).get("Directory", [])
            if not directory:
                return False
            return int(directory[0].get("metadataAgentProviderGroupId") or 0) == group_id
