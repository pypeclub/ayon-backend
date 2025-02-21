from dataclasses import dataclass
from typing import Any

from ayon_server.addons import AddonLibrary
from ayon_server.config import ayonconfig
from ayon_server.events import EventStream
from ayon_server.exceptions import BadRequestException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.settings import BaseSettingsModel


@dataclass
class SettingModifyContext:
    schema: str
    addon_name: str
    addon_version: str
    data: dict[str, Any] | None = None
    variant: str = "production"
    user_name: str | None = None
    project_name: str | None = None
    site_id: str | None = None


async def load_settings_from_context(
    context: SettingModifyContext,
) -> BaseSettingsModel | None:
    addon = AddonLibrary.addon(context.addon_name, context.addon_version)
    model = addon.get_settings_model()
    if not model:
        msg = f"{addon} does not have settings"
        raise BadRequestException(msg)

    settings: BaseSettingsModel | None = None
    if context.project_name:
        if context.site_id and context.user_name:
            settings = await addon.get_project_site_settings(
                project_name=context.project_name,
                user_name=context.user_name,
                site_id=context.site_id,
                variant=context.variant,
            )

        else:
            settings = await addon.get_project_settings(
                context.project_name,
                variant=context.variant,
            )
    else:
        settings = await addon.get_studio_settings(variant=context.variant)
    return settings


def get_delete_query(context: SettingModifyContext) -> tuple[str, tuple[Any, ...]]:
    query = f"""
        DELETE FROM {context.schema}.settings
        WHERE addon_name = $1 AND addon_version = $2 AND variant = $3
        RETURNING data AS original_data, NULL AS updated_data;
    """
    args = (context.addon_name, context.addon_version, context.variant)
    return query, args


def get_upsert_query(context: SettingModifyContext) -> tuple[str, tuple[Any, ...]]:
    query = f"""
        WITH existing AS (
            SELECT data AS original_data
            FROM {context.schema}.settings
            WHERE addon_name = $1 AND addon_version = $2 AND variant = $3
        )
        INSERT INTO {context.schema}.settings (addon_name, addon_version, variant, data)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (addon_name, addon_version, variant)
        DO UPDATE SET data = $4
        RETURNING
            (SELECT original_data FROM existing) AS original_data,
            settings.data AS updated_data;
    """
    args = (context.addon_name, context.addon_version, context.variant, context.data)
    return query, args


def get_site_delete_query(context: SettingModifyContext) -> tuple[str, tuple[Any, ...]]:
    query = f"""
        DELETE FROM {context.schema}.project_site_settings
        WHERE addon_name = $1
        AND addon_version = $2
        AND site_id = $3
        AND user_name = $4
    """
    args = (
        context.addon_name,
        context.addon_version,
        context.site_id,
        context.user_name,
    )
    return query, args


def get_site_upsert_query(context: SettingModifyContext) -> tuple[str, tuple[Any, ...]]:
    query = f"""
        WITH existing AS (
            SELECT data AS original_data
            FROM {context.schema}.project_site_settings
            WHERE
                addon_name = $1
            AND addon_version = $2
            AND site_id = $3
            AND user_name = $4
        )
        INSERT INTO {context.schema}.project_site_settings (
            addon_name,
            addon_version,
            site_id,
            user_name,
            data
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (addon_name, addon_version, site_id, user_name)
        DO UPDATE SET data = $5
        RETURNING
            (SELECT original_data FROM existing) AS original_data,
            project_site_settings.data AS updated_data;
    """
    args = (
        context.addon_name,
        context.addon_version,
        context.site_id,
        context.user_name,
        context.data,
    )
    return query, args


async def set_addon_settings(
    addon_name: str,
    addon_version: str,
    data: dict[str, Any] | None,
    *,
    project_name: str | None = None,
    user_name: str | None = None,
    site_id: str | None = None,
    variant: str = "production",
) -> None:
    """Save addon settings to the database.

    This saves given addon settings to the database and triggers settings.changed event
    and calls Addon.on_settings_changed method.

    `data` must be a dictionary with overrides, not the full settings,
    otherwise all settings will be marked as overridden.

    If `project_name` is specified, the settings will be saved for the project,
    otherwise they will be saved for the studio.
    """

    # Make sure we are not setting settings for a non-existent addon
    # This would raise NotFoundException if the addon does not exist

    context = SettingModifyContext(
        schema="public" if project_name is None else f"project_{project_name}",
        addon_name=addon_name,
        addon_version=addon_version,
        data=data,
        variant=variant,
        user_name=user_name,
        project_name=project_name,
        site_id=site_id,
    )

    # Load the original settings
    # This is needed only to call Addon.on_settings_changed method
    # which won't be needed in the future
    # (we should use subscribe to settings.changed event instead)

    original_settings = await load_settings_from_context(context)

    # Get the right query

    if not data:
        if site_id and user_name:
            query, args = get_site_delete_query(context)
        else:
            query, args = get_delete_query(context)
    else:
        if site_id and user_name:
            query, args = get_site_upsert_query(context)
        else:
            query, args = get_upsert_query(context)

    # Run the query

    res = await Postgres.fetch(query, *args)
    if not res:
        logger.warning(
            f"Addon settings not found: {addon_name} {addon_version} {variant}"
        )

    original_data = res[0]["original_data"] if res else None
    updated_data = res[0]["updated_data"] if res else None

    # Dispatch settings.changed event

    if ayonconfig.audit_trail:
        payload = {
            "originalValue": original_data or {},
            "newValue": updated_data or {},
        }

    otype = "project " if project_name else "studio "
    if site_id and user_name:
        variant = "site"
        otype = f"({site_id}) "
    description = f"{addon_name} {addon_version} {variant} {otype}overrides changed"

    await EventStream.dispatch(
        topic="settings.changed",
        description=description,
        summary={
            "addon_name": addon_name,
            "addon_version": addon_version,
            "variant": variant if not site_id else None,
            "site_id": site_id,
        },
        user=user_name,
        project=project_name,
        payload=payload,
    )

    # Call Addon.on_settings_changed
    # This is actually deprecated and should be removed in the future
    # since it won't affect all replicas of the addon

    new_settings = await load_settings_from_context(context)
    if original_settings and new_settings:
        addon = AddonLibrary.addon(addon_name, addon_version)
        await addon.on_settings_changed(
            original_settings,
            new_settings,
            variant=variant,
            project_name=project_name,
            user_name=user_name,
            site_id=site_id,
        )
