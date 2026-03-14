from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
import httpx

from sportscanner import __version__
from sportscanner.assets import cache_asset_file
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


def _asset_url_builder(request: Request):
    def build(entity_type: str, entity_id: str, asset_type: str, source_url: str) -> str:
        return str(
            request.url_for(
                "provider_asset",
                entity_type=entity_type,
                entity_id=entity_id,
                asset_type=asset_type,
            ).include_query_params(source_url=source_url)
        )

    return build


@router.post("/library/metadata/matches")
async def metadata_matches(request: Request) -> dict[str, Any]:
    context = ProviderContext.from_request(request)
    payload = await parse_match_request(request)
    service = ProviderMatchService(
        session_factory=context.session_factory,
        provider_identifier=context.provider_identifier,
        asset_url_builder=_asset_url_builder(request),
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
        asset_url_builder=_asset_url_builder(request),
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
        asset_url_builder=_asset_url_builder(request),
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
        asset_url_builder=_asset_url_builder(request),
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
        asset_url_builder=_asset_url_builder(request),
    )
    images = service.images(rating_key)
    return _container(context, size=len(images), totalSize=len(images), Image=images)


@router.get("/assets/{entity_type}/{entity_id}/{asset_type}", name="provider_asset")
def provider_asset(
    request: Request,
    entity_type: str,
    entity_id: str,
    asset_type: str,
    source_url: str,
) -> FileResponse:
    services = request.app.state.services
    try:
        with services.session_factory() as session:
            _asset, path, media_type = cache_asset_file(
                session,
                services.settings,
                entity_type=entity_type,
                entity_id=entity_id,
                asset_type=asset_type,
                source_url=source_url,
            )
            session.commit()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch artwork: {exc}") from exc
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
