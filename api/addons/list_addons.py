from typing import Any, Literal

import semver
from fastapi import Query, Request

from ayon_server.addons import AddonLibrary
from ayon_server.addons.models import SourceInfo, SourceInfoTypes
from ayon_server.api.dependencies import CurrentUser
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel

from .router import route_meta, router


class VersionInfo(OPModel):
    has_settings: bool = Field(default=False)
    has_site_settings: bool = Field(default=False)
    frontend_scopes: dict[str, Any] = Field(default_factory=dict)
    client_pyproject: dict[str, Any] | None = Field(None)
    client_source_info: list[SourceInfo] | None = Field(None)
    services: dict[str, Any] | None = Field(None)
    is_broken: bool = Field(False)
    reason: dict[str, str] | None = Field(None)


class AddonListItem(OPModel):
    name: str = Field(..., description="Machine friendly name of the addon")
    title: str = Field(..., description="Human friendly title of the addon")
    versions: dict[str, VersionInfo] = Field(
        ..., description="List of available versions"
    )
    description: str = Field(..., description="Addon description")
    production_version: str | None = Field(
        None,
        description="Production version of the addon",
    )
    staging_version: str | None = Field(
        None,
        description="Staging version of the addon",
    )
    addon_type: Literal["server", "pipeline"] = Field(
        ..., description="Type of the addon"
    )
    system: bool | None = Field(None, description="Is the addon a system addon?")


class AddonList(OPModel):
    addons: list[AddonListItem] = Field(..., description="List of available addons")


@router.get("", response_model_exclude_none=True, **route_meta)
async def list_addons(
    request: Request,
    user: CurrentUser,
    details: bool = Query(False, title="Show details"),
) -> AddonList:
    """List all available addons."""

    base_url = f"{request.url.scheme}://{request.url.netloc}"

    result = []
    library = AddonLibrary.getinstance()

    # maybe some ttl here?
    active_versions = await library.get_active_versions()

    for definition in library.data.values():
        vers = active_versions.get(definition.name, {})
        versions = {}
        is_system = False
        items = list(definition.versions.items())
        items.sort(key=lambda x: semver.VersionInfo.parse(x[0]))
        for version, addon in items:
            if addon.system:
                if not user.is_admin:
                    continue
                is_system = True

            vinf = {
                "has_settings": bool(addon.get_settings_model()),
                "has_site_settings": bool(addon.get_site_settings_model()),
                "frontend_scopes": await addon.get_frontend_scopes(),
            }
            if details:
                vinf["client_pyproject"] = await addon.get_client_pyproject()

                source_info = await addon.get_client_source_info(base_url=base_url)
                if source_info is None:
                    pass

                elif not all(isinstance(x, SourceInfoTypes) for x in source_info):
                    logger.error(f"Invalid source info for {addon.name} {version}")
                    source_info = [
                        x for x in source_info if isinstance(x, SourceInfoTypes)
                    ]
                vinf["client_source_info"] = source_info

                vinf["services"] = addon.services or None
            versions[version] = VersionInfo(**vinf)

        for version, reason in library.get_broken_versions(definition.name).items():
            versions[version] = VersionInfo(is_broken=True, reason=reason)

        if not versions:
            continue

        result.append(
            AddonListItem(
                name=definition.name,
                title=definition.friendly_name,
                versions=versions,
                description=definition.__doc__ or "",
                production_version=vers.get("production"),
                system=is_system or None,
                staging_version=vers.get("staging"),
                addon_type=addon.addon_type,
            )
        )
    result.sort(key=lambda x: x.name)
    return AddonList(addons=result)
