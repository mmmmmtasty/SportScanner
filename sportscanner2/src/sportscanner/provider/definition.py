from __future__ import annotations

from sportscanner.provider.schemas import MediaProviderModel, MediaProviderResponseModel, ProviderFeatureModel, ProviderSchemeModel, ProviderTypeModel


def build_provider_response(*, identifier: str, title: str, version: str) -> dict[str, object]:
    response = MediaProviderResponseModel(
        MediaProvider=MediaProviderModel(
            identifier=identifier,
            title=title,
            version=version,
            Types=[
                ProviderTypeModel(type=2, Scheme=[ProviderSchemeModel(scheme=identifier)]),
                ProviderTypeModel(type=3, Scheme=[ProviderSchemeModel(scheme=identifier)]),
                ProviderTypeModel(type=4, Scheme=[ProviderSchemeModel(scheme=identifier)]),
            ],
            Feature=[
                ProviderFeatureModel(type="metadata", key="/library/metadata"),
                ProviderFeatureModel(type="match", key="/library/metadata/matches"),
            ],
        )
    )
    return response.model_dump(exclude_none=True)
