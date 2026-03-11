from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Literal

from pydantic import BaseModel


class ProviderSchemeModel(BaseModel):
    scheme: str


class ProviderTypeModel(BaseModel):
    type: int
    Scheme: list[ProviderSchemeModel]


class ProviderFeatureModel(BaseModel):
    key: str
    type: str


class MediaProviderModel(BaseModel):
    identifier: str
    title: str
    version: str
    Types: list[ProviderTypeModel]
    Feature: list[ProviderFeatureModel]


class MediaProviderResponseModel(BaseModel):
    MediaProvider: MediaProviderModel


class MetadataItemModel(BaseModel):
    ratingKey: str
    guid: str
    key: str
    type: Literal["show", "season", "episode"]
    title: str
    score: int | None = None
    summary: str | None = None
    year: int | None = None
    index: int | None = None
    leafCount: int | None = None
    parentKey: str | None = None
    parentGuid: str | None = None
    parentIndex: int | None = None
    parentRatingKey: str | None = None
    parentTitle: str | None = None
    parentType: Literal["show", "season"] | None = None
    parentThumb: str | None = None
    grandparentKey: str | None = None
    grandparentGuid: str | None = None
    grandparentRatingKey: str | None = None
    grandparentTitle: str | None = None
    grandparentType: Literal["show"] | None = None
    grandparentThumb: str | None = None
    originallyAvailableAt: date_cls | None = None
    thumb: str | None = None
    art: str | None = None
    Children: ChildrenModel | None = None


class ChildrenModel(BaseModel):
    size: int
    Metadata: list[MetadataItemModel]


class ImageModel(BaseModel):
    type: Literal["background", "backgroundSquare", "clearLogo", "coverPoster", "snapshot"]
    url: str
    provider: str = "sportscanner2"
    alt: str | None = None


class MediaContainerModel(BaseModel):
    identifier: str
    size: int
    totalSize: int | None = None
    offset: int = 0
    Metadata: list[MetadataItemModel] | None = None
    Image: list[ImageModel] | None = None


class MatchRequestModel(BaseModel):
    type: Literal[2, 3, 4] | None = None
    title: str | None = None
    guid: str | None = None
    parentIndex: int | None = None
    index: int | None = None
    grandparentTitle: str | None = None
    parentTitle: str | None = None
    year: int | None = None
    date: date_cls | None = None
    manual: bool = False
    includeChildren: bool = False
    filename: str | None = None
    metadata: dict[str, Any] | None = None


MetadataItemModel.model_rebuild()


class MediaContainerResponseModel(BaseModel):
    MediaContainer: MediaContainerModel
