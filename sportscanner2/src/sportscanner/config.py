from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PLEX_PROVIDER_IDENTIFIER_PREFIX = "tv.plex.agents."
_PLEX_PROVIDER_IDENTIFIER_RE = re.compile(r"^tv\.plex\.agents\.[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*$")


def validate_plex_provider_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Provider Identifier In Plex is required.")
    if not normalized.startswith(PLEX_PROVIDER_IDENTIFIER_PREFIX):
        raise ValueError("Provider Identifier In Plex must start with tv.plex.agents.")
    if not _PLEX_PROVIDER_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError("Provider Identifier In Plex may contain only letters, numbers, and periods.")
    return normalized


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SPORTSCANNER_",
        extra="ignore",
        populate_by_name=True,
    )

    db_path: Path = Field(default=Path("/data/sportscanner.db"))
    incoming_dir: Path = Field(default=Path("/incoming"))
    library_dir: Path = Field(default=Path("/library"))
    asset_cache_dir: Path = Field(default=Path("/data/cache"))
    log_level: str = Field(default="info")
    watcher_debounce_seconds: float = Field(default=5.0)
    tsdb_api_mode: Literal["auto", "v1", "v2"] = Field(default="auto")
    tsdb_api_key: str = Field(default="", alias="TSDB_API_KEY")
    pms_url: str | None = Field(default=None)
    pms_token: str | None = Field(default=None)
    provider_public_url: str | None = Field(default=None)
    plex_provider_identifier: str = Field(default="tv.plex.agents.custom.sportscanner.metadata")
    plex_provider_group_name: str = Field(default="SportScanner 2")

    @field_validator("plex_provider_identifier")
    @classmethod
    def _validate_plex_provider_identifier(cls, value: str) -> str:
        return validate_plex_provider_identifier(value)

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
