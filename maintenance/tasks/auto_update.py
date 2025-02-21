import time

import httpx

from ayon_server.addons.library import AddonLibrary
from ayon_server.config import ayonconfig
from ayon_server.exceptions import NotFoundException
from ayon_server.helpers.cloud import CloudUtils
from ayon_server.helpers.download_addon import download_addon
from ayon_server.helpers.get_downloaded_addons import get_downloaded_addons
from ayon_server.helpers.migrate_addon_settings import migrate_addon_settings
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.version import __version__ as ayon_version
from maintenance.maintenance_task import StudioMaintenanceTask


async def get_required_addons() -> list[dict[str, str]]:
    url = f"{ayonconfig.ynput_cloud_api_url}/api/v1/me"
    headers = await CloudUtils.get_api_headers()
    headers["X-Ayon-Version"] = ayon_version
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            data = response.json()
            return data.get("requiredAddons", [])
    except Exception:
        logger.debug("Failed to fetch required addons list")
        return []


async def get_download_url(addon_name: str, addon_version: str) -> str:
    headers = await CloudUtils.get_api_headers()
    headers["X-Ayon-Version"] = ayon_version

    async with httpx.AsyncClient(timeout=ayonconfig.http_timeout) as client:
        endpoint = f"addons/{addon_name}/{addon_version}"
        res = await client.get(
            f"{ayonconfig.ynput_cloud_api_url}/api/v1/market/{endpoint}",
            headers=headers,
        )
        data = res.json()
        return data["url"]


async def run_auto_update() -> None:
    required_addons = await get_required_addons()

    if not required_addons:
        return

    addon_library = AddonLibrary.getinstance()

    bundle_addons_patch = {}

    downloaded_addons = get_downloaded_addons()
    for addon_name, addon_version in required_addons:
        # Do we have the required addon downloaded?

        if (addon_name, addon_version) not in downloaded_addons:
            try:
                url = await get_download_url(addon_name, addon_version)
            except Exception:
                logger.debug(
                    f"Failed to get download URL for {addon_name} {addon_version}"
                )
                continue

            await download_addon(
                addon_name=addon_name,
                addon_version=addon_version,
                url=url,
                no_queue=True,
            )
            # Just download the addon. After restart, we'll be able to continue
            continue

        # Addon is downloaded. Check if it's active

        try:
            addon = addon_library.addon(addon_name, addon_version)
        except NotFoundException:
            # Addon is downloaded, but not active. Server restart is needed
            continue

        # Addon is active. Check if it's in the production bundle

        if await addon.is_production():
            # Addon is active and in production we don't need to do anything
            continue

        logger.debug(
            f"Required addon {addon_name} {addon_version} is not in production"
        )

        # Get the current production version of the addon

        production_addon = await addon_library.get_production_addon(addon_name)
        if production_addon is not None:
            # There is a different version of the addon in production

            logger.debug(
                f"Migrating {addon_name} settings "
                f" from {addon_version} to {production_addon.version}"
            )
            await migrate_addon_settings(
                source_addon=production_addon,
                target_addon=addon,
                source_variant="production",
                target_variant="production",
            )

        bundle_addons_patch[addon_name] = addon_version

    if not bundle_addons_patch:
        return

    # Get the current production bundle

    q = "SELECT name, data FROM bundles WHERE is_production = TRUE"
    production_bundle = await Postgres.fetchrow(q)
    if production_bundle:
        data = production_bundle["data"]
        data["addons"].update(bundle_addons_patch)

        q = "UPDATE bundles SET data = $1 WHERE name = $2"
        await Postgres.execute(q, data, production_bundle["name"])

        for addon_name, addon_version in bundle_addons_patch.items():
            logger.info(f"Updated production bundle with {addon_name} {addon_version}")

    else:
        ts = int(time.time())
        bundle_name = f"default_bundle_{ts}"
        bundle_data = {
            "addons": bundle_addons_patch,
            "dependency_packages": {},
            "installer_version": None,
        }
        q = "INSERT INTO bundles (name, data, is_production) VALUES ($1, $2, TRUE)"
        await Postgres.execute(q, bundle_name, bundle_data)

        for addon_name, addon_version in bundle_addons_patch.items():
            logger.info(f"Created production bundle with {addon_name} {addon_version}")


class AutoUpdate(StudioMaintenanceTask):
    description = "Checking for updates"

    async def main(self):
        try:
            _ = await CloudUtils.get_api_headers()
        except Exception:
            # not connected to cloud. do nothing
            return

        await run_auto_update()
