import asyncio
import inspect
import os
import traceback
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import semver

from ayon_server.addons import AddonLibrary
from ayon_server.api.frontend import init_frontend
from ayon_server.api.messaging import messaging
from ayon_server.api.static import addon_static_router
from ayon_server.api.system import clear_server_restart_required
from ayon_server.background.workers import background_workers
from ayon_server.config import ayonconfig
from ayon_server.events import EventStream
from ayon_server.initialize import ayon_init
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import log_traceback, logger
from ayon_server.utils import slugify
from maintenance.scheduler import MaintenanceScheduler

if TYPE_CHECKING:
    from fastapi import FastAPI

maintenance_scheduler = MaintenanceScheduler()


async def load_access_groups() -> None:
    """Load access groups from the database."""
    from ayon_server.access.access_groups import AccessGroups

    await AccessGroups.load()


def init_addon_endpoints(target_app: "FastAPI") -> None:
    library = AddonLibrary.getinstance()
    for addon_name, addon_definition in library.items():
        for version in addon_definition.versions:
            addon = addon_definition.versions[version]

            if hasattr(addon, "ws"):
                target_app.add_api_websocket_route(
                    f"/api/addons/{addon_name}/{version}/ws",
                    addon.ws,
                    name=f"{addon_name}_{version}_ws",
                )

            for router in addon.routers:
                target_app.include_router(
                    router,
                    prefix=f"/api/addons/{addon_name}/{version}",
                    tags=[f"{addon_definition.friendly_name} {version}"],
                    include_in_schema=ayonconfig.openapi_include_addon_endpoints,
                    generate_unique_id_function=lambda x: slugify(
                        f"{addon_name}_{version}_{x.name}", separator="_"
                    ),
                )

            for endpoint in addon.endpoints:
                path = endpoint["path"].lstrip("/")
                first_element = path.split("/")[0]
                # TODO: site settings? other routes?
                if first_element in ["settings", "schema", "overrides"]:
                    logger.error(f"Unable to assing path to endpoint: {path}")
                    continue

                path = f"/api/addons/{addon_name}/{version}/{path}"
                target_app.add_api_route(
                    path,
                    endpoint["handler"],
                    include_in_schema=ayonconfig.openapi_include_addon_endpoints,
                    methods=[endpoint["method"]],
                    name=endpoint["name"],
                    tags=[f"{addon_definition.friendly_name} {version}"],
                    operation_id=slugify(
                        f"{addon_name}_{version}_{endpoint['name']}",
                        separator="_",
                    ),
                )


def init_addon_static(target_app: "FastAPI") -> None:
    """Serve static files for addon frontends."""

    target_app.include_router(addon_static_router)


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    _ = app
    # Save the process PID
    with open("/var/run/ayon.pid", "w") as f:
        f.write(str(os.getpid()))

    await ayon_init()
    await load_access_groups()

    # Start background tasks

    background_workers.start()
    messaging.start()
    maintenance_scheduler.start()

    # Initialize addons

    start_event = await EventStream.dispatch("server.started", finished=False)

    library = AddonLibrary.getinstance()
    addon_records = list(AddonLibrary.items())
    if library.restart_requested:
        logger.warning("Restart requested, skipping addon setup")
        await EventStream.dispatch(
            "server.restart_requested",
            description="Server restart requested during addon initialization",
        )
        return

    restart_requested = False
    bad_addons = {}
    for addon_name, addon in addon_records:
        for version in addon.versions.values():
            try:
                if inspect.iscoroutinefunction(version.pre_setup):
                    # Since setup may, but does not have to be async, we need to
                    # silence mypy here.
                    await version.pre_setup()
                else:
                    version.pre_setup()
                if (not restart_requested) and version.restart_requested:
                    logger.warning(
                        f"Restart requested during addon {addon_name} pre-setup."
                    )
                    restart_requested = True
            except AssertionError as e:
                logger.error(
                    f"Unable to pre-setup addon {addon_name} {version.version}: {e}"
                )
                reason = {"error": str(e)}
                bad_addons[(addon_name, version.version)] = reason
            except Exception as e:
                log_traceback(f"Error during {addon_name} {version.version} pre-setup")
                reason = {
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
                bad_addons[(addon_name, version.version)] = reason

    for addon_name, addon in addon_records:
        for version in addon.versions.values():
            # This is a fix of a bug in the 1.0.4 and earlier versions of the addon
            # where automatic addon update triggers an error
            if addon_name == "ynputcloud" and semver.VersionInfo.parse(
                version.version
            ) < semver.VersionInfo.parse("1.0.5"):
                logger.debug(f"Skipping {addon_name} {version.version} setup.")
                continue

            try:
                if inspect.iscoroutinefunction(version.setup):
                    await version.setup()
                else:
                    version.setup()
                if (not restart_requested) and version.restart_requested:
                    logger.warning(
                        f"Restart requested during addon {addon_name} setup."
                    )
                    restart_requested = True
            except AssertionError as e:
                logger.error(
                    f"Unable to setup addon {addon_name} {version.version}: {e}"
                )
                reason = {"error": str(e)}
                bad_addons[(addon_name, version.version)] = reason
            except Exception as e:
                log_traceback(f"Error during {addon_name} {version.version} setup")
                reason = {
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
                bad_addons[(addon_name, version.version)] = reason

    for _addon_name, _addon_version in bad_addons:
        reason = bad_addons[(_addon_name, _addon_version)]
        library.unload_addon(_addon_name, _addon_version, reason=reason)

    if restart_requested:
        await EventStream.dispatch(
            "server.restart_requested",
            description="Server restart requested during addon setup",
        )
    else:
        # Initialize endpoints for active addons
        init_addon_endpoints(app)

        # Addon static dirs must stay exactly here
        init_addon_static(app)

        # Frontend must be initialized last (since it is mounted to /)
        init_frontend(app)

        if start_event is not None:
            await EventStream.update(
                start_event,
                status="finished",
                description="Server started",
            )

        asyncio.create_task(clear_server_restart_required())
        logger.info("Server is now ready to connect")

    yield

    logger.info("Server is shutting down")
    await background_workers.shutdown()
    await messaging.shutdown()
    await Postgres.shutdown()
    logger.info("Server stopped", handlers=None)
