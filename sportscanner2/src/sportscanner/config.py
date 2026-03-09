from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    plex_provider_group_name: str = Field(default="SportScanner 2")

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

