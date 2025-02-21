import copy
from typing import Any

from ayon_server.addons import AddonLibrary
from ayon_server.api.dependencies import CurrentUser, SiteID
from ayon_server.api.responses import EmptyResponse
from ayon_server.exceptions import NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.settings.postprocess import postprocess_settings_schema
from nxtools import logging

from .router import route_meta, router


@router.get("/{addon_name}/{version}/siteSettings/schema", **route_meta)
async def get_addon_site_settings_schema(
    addon_name: str,
    version: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """Return the JSON schema of the addon site settings."""

    if (addon := AddonLibrary.addon(addon_name, version)) is None:
        raise NotFoundException(f"Addon {addon_name} {version} not found")

    model = addon.get_site_settings_model()

    if model is None:
        logging.error(f"No site settings schema for addon {addon_name}")
        return {}

    schema = copy.deepcopy(model.schema())
    context = {
        "addon": addon,
        "user_name": user.name,
    }

    await postprocess_settings_schema(schema, model, context=context)
    schema["title"] = addon.friendly_name
    return schema


# TODO: consider adding an optional user_name query parameter to
# allow managers and admins to retrieve site_overrides of other users


@router.get("/{addon_name}/{version}/siteSettings", **route_meta)
async def get_addon_site_settings(
    addon_name: str,
    version: str,
    user: CurrentUser,
    site_id: SiteID,
) -> dict[str, Any]:
    """Return the JSON schema of the addon site settings."""

    if (addon := AddonLibrary.addon(addon_name, version)) is None:
        raise NotFoundException(f"Addon {addon_name} {version} not found")

    model = addon.get_site_settings_model()

    if model is None:
        logging.error(f"No site settings schema for addon {addon_name}")
        return {}

    data = {}
    query = """
        SELECT data FROM site_settings
        WHERE site_id = $1 AND addon_name = $2
        AND addon_version = $3 AND user_name = $4
    """
    async for row in Postgres.iterate(query, site_id, addon_name, version, user.name):
        data = row["data"]

    # use model to include defaults
    return model(**data)  # type: ignore


@router.put("/{addon_name}/{version}/siteSettings", status_code=204, **route_meta)
async def set_addon_site_settings(
    payload: dict[str, Any],
    addon_name: str,
    version: str,
    user: CurrentUser,
    site_id: SiteID,
) -> EmptyResponse:
    if (addon := AddonLibrary.addon(addon_name, version)) is None:
        raise NotFoundException(f"Addon {addon_name} {version} not found")

    model = addon.get_site_settings_model()

    if model is None:
        logging.error(f"No site settings schema for addon {addon_name}")
        return EmptyResponse()

    data = model(**payload)

    await Postgres.execute(
        """
        INSERT INTO site_settings (addon_name, addon_version, site_id, user_name, data)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (addon_name, addon_version, site_id, user_name)
        DO UPDATE SET data = $5
        """,
        addon_name,
        version,
        site_id,
        user.name,
        data.dict(),
    )

    return EmptyResponse()
