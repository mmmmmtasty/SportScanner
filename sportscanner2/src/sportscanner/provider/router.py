from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sportscanner import __version__
from sportscanner.provider.context import ProviderContext
from sportscanner.provider.definition import build_provider_response
from sportscanner.provider.match_service import ProviderMatchService
from sportscanner.provider.metadata_service import ProviderMetadataService
from sportscanner.provider.request import paginate, parse_match_request, truthy
from sportscanner.provider.schemas import MediaContainerModel, MediaContainerResponseModel

router = APIRouter(prefix="/provider/tv", tags=["provider"])


def _container(context: ProviderContext, **kwargs: Any) -> dict[str, Any]:
    response = MediaContainerResponseModel(
        MediaContainer=MediaContainerModel(
            identifier=context.provider_identifier,
            **kwargs,
        )
    )
    return response.model_dump(exclude_none=True)


@router.get("")
def provider_root(request: Request) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    return build_provider_response(
        identifier=context.provider_identifier,
        title=context.provider_title,
        version=__version__,
    )


@router.post("/library/metadata/matches")
async def metadata_matches(request: Request) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    payload = await parse_match_request(request)
    service = ProviderMatchService(
        session_factory=context.session_factory,
        provider_identifier=context.provider_identifier,
    )
    items = service.match(payload)
    paged, start, total = paginate(request, items)
    return _container(context, size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}")
def metadata_by_rating_key(request: Request, rating_key: str) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    service = ProviderMetadataService(
        session_factory=context.session_factory,
        provider_identifier=context.provider_identifier,
    )
    item = service.by_rating_key(
        rating_key,
        include_children=truthy(request.query_params.get("includeChildren")),
    )
    return _container(context, size=1, totalSize=1, Metadata=[item])


@router.get("/library/metadata/{rating_key}/children")
def metadata_children(request: Request, rating_key: str) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    service = ProviderMetadataService(
        session_factory=context.session_factory,
        provider_identifier=context.provider_identifier,
    )
    items = service.children(rating_key)
    paged, start, total = paginate(request, items)
    return _container(context, size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}/grandchildren")
def metadata_grandchildren(request: Request, rating_key: str) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    service = ProviderMetadataService(
        session_factory=context.session_factory,
        provider_identifier=context.provider_identifier,
    )
    items = service.grandchildren(rating_key)
    paged, start, total = paginate(request, items)
    return _container(context, size=len(paged), totalSize=total, offset=start, Metadata=paged)


@router.get("/library/metadata/{rating_key}/images")
def metadata_images(request: Request, rating_key: str) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    service = ProviderMetadataService(
        session_factory=context.session_factory,
        provider_identifier=context.provider_identifier,
    )
    images = service.images(rating_key)
    return _container(context, size=len(images), totalSize=len(images), Image=images)
