from __future__ import annotations

from hashlib import sha256
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscanner.config import Settings
from sportscanner.db.models import Asset


def get_or_create_asset(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    asset_type: str,
    source_url: str,
    sort_order: int = 0,
) -> Asset:
    asset = session.scalar(
        select(Asset).where(
            Asset.entity_type == entity_type,
            Asset.entity_id == entity_id,
            Asset.asset_type == asset_type,
            Asset.sort_order == sort_order,
        )
    )
    if asset is None:
        asset = Asset(
            entity_type=entity_type,
            entity_id=entity_id,
            asset_type=asset_type,
            source_url=source_url,
            sort_order=sort_order,
        )
        session.add(asset)
        session.flush()
        return asset

    if asset.source_url != source_url:
        asset.source_url = source_url
        asset.cached_path = None
    session.flush()
    return asset


def cache_asset_file(
    session: Session,
    settings: Settings,
    *,
    entity_type: str,
    entity_id: str,
    asset_type: str,
    source_url: str,
    sort_order: int = 0,
) -> tuple[Asset, Path, str | None]:
    asset = get_or_create_asset(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_type=asset_type,
        source_url=source_url,
        sort_order=sort_order,
    )
    existing_path = Path(asset.cached_path) if asset.cached_path else None
    if existing_path is not None and existing_path.exists():
        return asset, existing_path, _media_type(existing_path)

    response = httpx.get(source_url, timeout=20.0, follow_redirects=True)
    response.raise_for_status()

    target_dir = settings.asset_cache_dir / _safe_path_component(entity_type) / _safe_path_component(entity_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = _asset_suffix(source_url, response.headers.get("content-type"))
    filename = f"{_safe_path_component(asset_type)}-{sha256(source_url.encode('utf-8')).hexdigest()[:16]}{suffix}"
    target_path = target_dir / filename
    temp_path = target_dir / f".{filename}.tmp"
    temp_path.write_bytes(response.content)
    temp_path.replace(target_path)

    if existing_path is not None and existing_path != target_path and existing_path.exists():
        existing_path.unlink(missing_ok=True)

    asset.cached_path = str(target_path)
    session.flush()
    return asset, target_path, response.headers.get("content-type") or _media_type(target_path)


def _safe_path_component(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return sanitized or "asset"


def _asset_suffix(source_url: str, content_type: str | None) -> str:
    path_suffix = Path(urlparse(source_url).path).suffix
    if path_suffix:
        return path_suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip(), strict=False)
        if guessed:
            return guessed
    return ".img"


def _media_type(path: Path) -> str | None:
    media_type, _encoding = mimetypes.guess_type(str(path), strict=False)
    return media_type
