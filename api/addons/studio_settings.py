import copy
from typing import Any

from fastapi import Query
from pydantic.error_wrappers import ValidationError

from ayon_server.addons import AddonLibrary
from ayon_server.api.dependencies import CurrentUser
from ayon_server.api.responses import EmptyResponse
from ayon_server.exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
)
from ayon_server.lib.postgres import Postgres
from ayon_server.settings.overrides import extract_overrides, list_overrides
from ayon_server.settings.postprocess import postprocess_settings_schema
from ayon_server.settings.set_addon_settings import set_addon_settings

from .common import ModifyOverridesRequestModel, pin_override, remove_override
from .router import route_meta, router


@router.get("/{addon_name}/{addon_version}/schema", **route_meta)
async def get_addon_settings_schema(
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
) -> dict[str, Any]:
    """Return the JSON schema of the addon settings."""

    if (addon := AddonLibrary.addon(addon_name, addon_version)) is None:
        raise NotFoundException(f"Addon {addon_name} {addon_version} not found")

    model = addon.get_settings_model()

    if model is None:
        return {}

    context = {
        "addon": addon,
        "settings_variant": variant,
        "user_name": user.name,
    }

    schema = copy.deepcopy(model.schema())
    await postprocess_settings_schema(schema, model, context=context)
    schema["title"] = addon.friendly_name
    return schema


@router.get(
    "/{addon_name}/{addon_version}/settings",
    response_model=dict[str, Any],
    **route_meta,
)
async def get_addon_studio_settings(
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
    as_version: str | None = Query(None, alias="as"),
) -> dict[str, Any]:
    """Return the settings (including studio overrides) of the given addon."""

    if (addon := AddonLibrary.addon(addon_name, addon_version)) is None:
        raise NotFoundException(f"Addon {addon_name} {addon_version} not found")

    settings = await addon.get_studio_settings(variant=variant, as_version=as_version)
    if not settings:
        return {}
    return settings  # type: ignore


@router.post("/{addon_name}/{addon_version}/settings", status_code=204, **route_meta)
async def set_addon_studio_settings(
    payload: dict[str, Any],
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
) -> EmptyResponse:
    """Set the studio overrides for the given addon."""

    if not user.is_manager:
        raise ForbiddenException

    explicit_pins = payload.pop("__pinned_fields__", None)
    explicit_unpins = payload.pop("__unpinned_fields__", None)

    addon = AddonLibrary.addon(addon_name, addon_version)
    original = await addon.get_studio_settings(variant=variant)
    existing = await addon.get_studio_overrides(variant=variant)
    model = addon.get_settings_model()
    if (original is None) or (model is None):
        raise BadRequestException("This addon does not have settings")
    try:
        data = extract_overrides(
            original,
            model(**payload),
            existing=existing,
            explicit_pins=explicit_pins,
            explicit_unpins=explicit_unpins,
        )
    except ValidationError as e:
        raise BadRequestException("Invalid settings", errors=e.errors()) from e

    await set_addon_settings(
        addon_name,
        addon_version,
        data,
        variant=variant,
    )

    return EmptyResponse()


@router.get("/{addon_name}/{addon_version}/overrides", **route_meta)
async def get_addon_studio_overrides(
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
    as_version: str | None = Query(None, alias="as"),
):
    if not user.is_manager:
        raise ForbiddenException

    addon = AddonLibrary.addon(addon_name, addon_version)
    settings = await addon.get_studio_settings(variant=variant, as_version=as_version)
    if settings is None:
        return {}
    overrides = await addon.get_studio_overrides(variant=variant, as_version=as_version)
    return list_overrides(settings, overrides)


@router.delete("/{addon_name}/{addon_version}/overrides", status_code=204, **route_meta)
async def delete_addon_studio_overrides(
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
) -> EmptyResponse:
    if not user.is_manager:
        raise ForbiddenException

    await set_addon_settings(
        addon_name,
        addon_version,
        None,
        variant=variant,
    )
    return EmptyResponse()


@router.post("/{addon_name}/{addon_version}/overrides", status_code=204, **route_meta)
async def modify_studio_overrides(
    payload: ModifyOverridesRequestModel,
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
) -> EmptyResponse:
    if not user.is_manager:
        raise ForbiddenException

    if payload.action == "delete":
        await remove_override(addon_name, addon_version, payload.path, variant=variant)
    elif payload.action == "pin":
        await pin_override(addon_name, addon_version, payload.path, variant=variant)

    return EmptyResponse()


#
# Raw overrides. No validation or processing is done on these.
#


@router.get("/{addon_name}/{addon_version}/rawOverrides", **route_meta)
async def get_raw_addon_studio_overrides(
    addon_name: str,
    addon_version: str,
    user: CurrentUser,
    variant: str = Query("production"),
) -> dict[str, Any]:
    if not user.is_admin:
        raise ForbiddenException("Only admins can access raw overrides")

    result = await Postgres.fetch(
        """
        SELECT data FROM settings
        WHERE addon_name = $1
        AND addon_version = $2
        AND variant = $3
        """,
        addon_name,
        addon_version,
        variant,
    )

    if not result:
        return {}

    return result[0]["data"]


@router.put("/{addon_name}/{addon_version}/rawOverrides", status_code=204, **route_meta)
async def set_raw_addon_studio_overrides(
    addon_name: str,
    addon_version: str,
    payload: dict[str, Any],
    user: CurrentUser,
    variant: str = Query("production"),
) -> EmptyResponse:
    """Set raw studio overrides for an addon.

    Warning: this endpoint is not intended for general use and should only be used by
    administrators. It bypasses the normal validation and processing that occurs when
    modifying studio overrides through the normal API.

    It won't trigger any events or validation checks, and may result in unexpected
    behaviour if used incorrectly.
    """
    if not user.is_admin:
        raise ForbiddenException("Only admins can access raw overrides")

    await Postgres.execute(
        """
        INSERT INTO settings
            (addon_name, addon_version, variant, data)
        VALUES
            ($1, $2, $3, $4)
        ON CONFLICT (addon_name, addon_version, variant)
            DO UPDATE SET data = $4
        """,
        addon_name,
        addon_version,
        variant,
        payload,
    )
    return EmptyResponse()
