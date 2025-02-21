__all__ = ["migrate_settings"]

from ayon_server.addons.library import AddonLibrary
from ayon_server.events import EventStream
from ayon_server.exceptions import NotFoundException
from ayon_server.helpers.migrate_addon_settings import migrate_addon_settings
from ayon_server.lib.postgres import Connection, Postgres
from nxtools import logging

AddonVersionsDict = dict[str, str]


async def _get_bundles_addons(
    source_bundle: str,
    target_bundle: str,
    conn: Connection,
) -> tuple[AddonVersionsDict, AddonVersionsDict]:
    """Get addons for source and target bundles."""

    source_addons: AddonVersionsDict | None = None
    target_addons: AddonVersionsDict | None = None
    res = await conn.fetch(
        "SELECT name, data FROM bundles WHERE name = ANY($1)",
        [source_bundle, target_bundle],
    )
    for row in res:
        if row["name"] == source_bundle:
            source_addons = row["data"].get("addons", {})
        if row["name"] == target_bundle:
            target_addons = row["data"].get("addons", {})

    if not source_addons:
        raise NotFoundException(f"Source bundle {source_bundle} not found")
    elif not target_addons:
        raise NotFoundException(f"Target bundle {target_bundle} not found")

    # remove addons that has no version
    source_addons = {k: v for k, v in source_addons.items() if v}
    target_addons = {k: v for k, v in target_addons.items() if v}

    if not source_addons:
        raise NotFoundException(f"Source bundle {source_bundle} has no addons")
    elif not target_addons:
        raise NotFoundException(f"Target bundle {target_bundle} has no addons")

    return source_addons, target_addons


async def _migrate_settings_by_bundle(
    source_bundle: str,
    target_bundle: str,
    source_variant: str,
    target_variant: str,
    with_projects: bool,
    user_name: str | None,
    conn: Connection,
) -> None:
    """
    Perform migration of settings from source to
    target bundle in a given transaction.
    """
    source_addons, target_addons = await _get_bundles_addons(
        source_bundle, target_bundle, conn
    )

    # get addons that are present in both source and target bundles
    # (i.e. addons that need to be migrated)
    addons_to_migrate = set(source_addons.keys()) & set(target_addons.keys())

    for addon_name in addons_to_migrate:
        source_version = source_addons[addon_name]
        target_version = target_addons[addon_name]

        # get addon instances

        try:
            source_addon = AddonLibrary.addon(addon_name, source_version)
        except NotFoundException:
            logging.warning(
                f"Source addon {addon_name} version {source_version} is not installed"
            )
            continue

        try:
            target_addon = AddonLibrary.addon(addon_name, target_version)
        except NotFoundException:
            logging.warning(
                f"Target addon {addon_name} version {target_version} is not installed"
            )
            continue

        # perform migration of addon settings

        events = await migrate_addon_settings(
            source_addon,
            target_addon,
            source_variant,
            target_variant,
            with_projects,
            conn,
        )

        for event in events:
            event["user"] = user_name
            await EventStream.dispatch("settings.changed", **event)


#
# The main function that is called from the API
#


async def migrate_settings(
    source_bundle: str,
    target_bundle: str,
    source_variant: str,
    target_variant: str,
    with_projects: bool = True,
    user_name: str | None = None,
    conn: Connection | None = None,
) -> None:
    if conn:
        await _migrate_settings_by_bundle(
            source_bundle,
            target_bundle,
            source_variant,
            target_variant,
            with_projects,
            user_name,
            conn,
        )

    else:
        async with Postgres.acquire() as _conn, _conn.transaction():
            await _migrate_settings_by_bundle(
                source_bundle,
                target_bundle,
                source_variant,
                target_variant,
                with_projects,
                user_name,
                _conn,
            )
