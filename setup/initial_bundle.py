from typing import Any

from ayon_server.events import EventStream
from ayon_server.installer.addons import install_addon_from_url
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger


async def create_initial_bundle(bundle_data: dict[str, Any]):
    """Download initial addons and create the first production bundle

    This function will be skipped if there is already a bundle in the database.

    initialBundle data should be in the following format:

    {
        "name": "InitialBundle",
        "addons": [
            {
                "url": "https://example.com/example-ayon-addon.1.0.0.zip",
                "name": "example",
                "version": "1.0.0"
            }
        ]
    }

    `name` is optional and defaults to "InitialBundle".

    Addons:

    When url is provided, the addon will be downloaded and installed.
    Name and version is then used just for the logging.

    When name and version is provided, but not the URL,
    server is expected to have the addon already installed.

    Existence of the addon IS NOT CHECKED, so make sure to provide correct data.
    This is intentional, as it allows for more flexibility in the initial bundle setup.

    TODO: Allow installing Launchers and dependency packages as well.
    """

    logger.debug("Checking for bundles")
    res = await Postgres.fetch("SELECT name FROM bundles LIMIT 1")
    if res:
        return

    logger.info("Creating initial bundle")
    addons = bundle_data.get("addons", [])
    bundle_addons = {}

    for i, addon in enumerate(addons):
        addon_name = addon.get("name")
        addon_version = addon.get("version")

        if addon_name and addon_version:
            log_name = f"{addon_name} {addon_version}"
        else:  # If name and version is not provided, use the index
            log_name = f"{i+1} of {len(addons)}"

        if addon_url := addon.get("url"):
            # Do not use helpers.download_addon here, as we need
            # the underlying function and await the download, while
            # helpers.download_addon just enqueues the download task
            # and finishes immediately. BackgroundInstaller doesn't
            # run at this point.

            logger.info(f"Installing addon {log_name}")
            event_id = await EventStream.dispatch(
                "addon.install_from_url",
                description="Installing addon from URL",
                summary={"url": addon_url},
                finished=False,
            )

            zip_info = await install_addon_from_url(event_id, addon_url)
            bundle_addons[zip_info.name] = zip_info.version

        elif addon_name and addon_version:
            bundle_addons[addon_name] = addon_version

    if not bundle_addons:
        logger.warning("No addons provided for the initial bundle")
        return

    bundle_name = bundle_data.get("name", "InitialBundle")
    bundle_data = {
        "addons": bundle_addons,
        "dependency_packages": {},
        "installer_version": None,
    }

    async with Postgres.acquire() as conn, conn.transaction():
        logger.info(f"Creating initial bundle '{bundle_name}'")
        await Postgres.execute("UPDATE bundles SET is_production = FALSE")

        query = """
            INSERT INTO bundles (name, data, is_production)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (name) DO UPDATE SET data = $2, is_production = TRUE
        """

        await Postgres.execute(query, bundle_name, bundle_data)
