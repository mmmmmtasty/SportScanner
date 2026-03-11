from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Request

from sportscanner.config import validate_plex_provider_identifier
from sportscanner.db.models import AppSetting


SessionFactory = Callable[[], object]


def _setting(request: Request, key: str, fallback: str) -> str:
    with request.app.state.services.session_factory() as session:
        setting = session.get(AppSetting, key)
    return setting.value if setting is not None else fallback


@dataclass(slots=True)
class ProviderContext:
    session_factory: SessionFactory
    provider_identifier: str
    provider_title: str

    @classmethod
    def from_request(cls, request: Request) -> "ProviderContext":
        configured_identifier = _setting(
            request,
            "plex_provider_identifier",
            request.app.state.services.settings.plex_provider_identifier,
        )
        try:
            provider_identifier = validate_plex_provider_identifier(configured_identifier)
        except ValueError:
            provider_identifier = request.app.state.services.settings.plex_provider_identifier

        provider_title = _setting(
            request,
            "plex_provider_group_name",
            request.app.state.services.settings.plex_provider_group_name,
        )

        return cls(
            session_factory=request.app.state.services.session_factory,
            provider_identifier=provider_identifier,
            provider_title=provider_title,
        )
