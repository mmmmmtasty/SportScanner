from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProviderFeature(BaseModel):
    key: str
    type: str
    method: str | None = None
    mediaTypes: list[int] | None = None


class MediaProviderModel(BaseModel):
    identifier: str
    title: str
    version: str
    features: list[ProviderFeature]


class SearchResultModel(BaseModel):
    id: str
    guid: str
    name: str
    score: int
    type: Literal["show", "season", "episode"] = "show"
    year: int | None = None


class MetadataItemModel(BaseModel):
    ratingKey: str
    guid: str
    key: str
    type: Literal["show", "season", "episode"]
    title: str
    summary: str | None = None
    index: int | None = None
    leafCount: int | None = None
    parentGuid: str | None = None
    parentIndex: int | None = None
    parentRatingKey: str | None = None
    parentTitle: str | None = None
    grandparentGuid: str | None = None
    grandparentRatingKey: str | None = None
    grandparentTitle: str | None = None
    originallyAvailableAt: date | None = None
    thumb: str | None = None
    art: str | None = None


class ImageModel(BaseModel):
    type: str
    url: str
    provider: str = "sportscanner2"
    alt: str | None = None


class MediaContainerModel(BaseModel):
    size: int
    totalSize: int | None = None
    offset: int = 0
    SearchResult: list[SearchResultModel] | None = None
    Metadata: list[MetadataItemModel] | None = None
    Image: list[ImageModel] | None = None


class MatchRequestModel(BaseModel):
    type: str | None = None
    title: str | None = None
    guid: str | None = None
    parentIndex: int | None = None
    index: int | None = None
    grandparentTitle: str | None = None
    parentTitle: str | None = None
    metadata: dict[str, Any] | None = None

