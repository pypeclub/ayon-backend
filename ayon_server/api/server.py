import asyncio
import importlib
import inspect
import os
import pathlib
import sys
import time
import traceback

import fastapi
import semver
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect

# okay. now the rest
from ayon_server.addons import AddonLibrary
from ayon_server.api.frontend import init_frontend
from ayon_server.api.messaging import Messaging
from ayon_server.api.metadata import app_meta, tags_meta
from ayon_server.api.postgres_exceptions import (
    IntegrityConstraintViolationError,
    parse_postgres_exception,
)
from ayon_server.api.responses import ErrorResponse
from ayon_server.api.static import addon_static_router
from ayon_server.api.system import clear_server_restart_required
from ayon_server.auth.session import Session
from ayon_server.background.log_collector import log_collector
from ayon_server.background.workers import background_workers
from ayon_server.config import ayonconfig
from ayon_server.events import EventStream
from ayon_server.exceptions import AyonException
from ayon_server.graphql import router as graphql_router
from ayon_server.initialize import ayon_init
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import log_traceback, logger
from ayon_server.utils import create_uuid, parse_access_token, slugify
from maintenance.scheduler import MaintenanceScheduler

# We just need the log collector to be initialized.
_ = log_collector
# But we need this. but depending on the AYON_RUN_MAINTENANCE
# environment variable, we might not run the maintenance tasks.
maintenance_scheduler = MaintenanceScheduler()

#
# Let's create the app
#

app = fastapi.FastAPI(
    docs_url=None,
    redoc_url="/docs" if not ayonconfig.disable_rest_docs else None,
    openapi_tags=tags_meta,
    **app_meta,
)


@app.middleware("http")
async def logging_middleware(request: fastapi.Request, call_next):
    request_id = create_uuid()
    context = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
    }
    with logger.contextualize(**context):
        start_time = time.perf_counter()
        logger.trace("Request started")
        response = await call_next(request)
        process_time = round(time.perf_counter() - start_time, 3)
        logger.trace("Request finished", process_time=process_time)

    return response


#
# Error handling
#


async def user_name_from_request(request: fastapi.Request) -> str:
    """Get user from request"""

    access_token = parse_access_token(request.headers.get("Authorization", ""))
    if not access_token:
        return "anonymous"
    try:
        session_data = await Session.check(access_token, None)
    except AyonException:
        return "anonymous"
    if not session_data:
        return "anonymous"
    user_name = session_data.user.name
    assert isinstance(user_name, str)
    return user_name


@app.exception_handler(404)
async def custom_404_handler(request: fastapi.Request, _):
    """Redirect 404s to frontend."""

    if request.url.path.startswith("/api"):
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "detail": f"API endpoint {request.url.path} not found",
                "path": request.url.path,
            },
        )

    elif request.url.path.startswith("/addons"):
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "detail": f"Addon endpoint {request.url.path} not found",
                "path": request.url.path,
            },
        )

    return fastapi.responses.JSONResponse(
        status_code=404,
        content={
            "code": 404,
            "detail": f"File {request.url.path} not found",
            "path": request.url.path,
        },
    )


@app.exception_handler(AyonException)
async def ayon_exception_handler(
    request: fastapi.Request,
    exc: AyonException,
) -> fastapi.responses.JSONResponse:
    if exc.status in [401, 403, 503]:
        # unauthorized, forbidden, service unavailable
        # we don't need any additional details for these
        return fastapi.responses.JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.status,
                "detail": exc.detail,
            },
        )

    user_name = await user_name_from_request(request)
    path = f"[{request.method.upper()}]"
    path += f" {request.url.path.removeprefix('/api')}"

    if exc.status == 500:
        logger.error(f"{path}: {exc}", user=user_name)
    else:
        logger.debug(f"{path}: {exc}", user=user_name)

    return fastapi.responses.JSONResponse(
        status_code=exc.status,
        content={
            "code": exc.status,
            "detail": exc.detail,
            "path": request.url.path,
            **exc.extra,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: fastapi.Request,
    exc: RequestValidationError,
) -> fastapi.responses.JSONResponse:
    logger.error(f"Validation error\n{exc}")
    detail = "Validation error"  # TODO: Be descriptive, but not too much
    return fastapi.responses.JSONResponse(
        status_code=400,
        content=ErrorResponse(code=400, detail=detail).dict(),
    )


@app.exception_handler(IntegrityConstraintViolationError)
async def integrity_constraint_violation_error_handler(
    request: fastapi.Request,
    exc: IntegrityConstraintViolationError,
) -> fastapi.responses.JSONResponse:
    path = f"[{request.method.upper()}]"
    path += f" {request.url.path.removeprefix('/api')}"

    tb = traceback.extract_tb(exc.__traceback__)
    fname, line_no, func, _ = tb[-1]

    payload = {
        "path": path,
        "file": fname,
        "function": func,
        "line": line_no,
        **parse_postgres_exception(exc),
    }

    return fastapi.responses.JSONResponse(status_code=500, content=payload)


@app.exception_handler(AssertionError)
async def assertion_exception_handler(request: fastapi.Request, exc: AssertionError):
    user_name = await user_name_from_request(request)
    path = f"[{request.method.upper()}]"
    path += f" {request.url.path.removeprefix('/api')}"

    tb = traceback.extract_tb(exc.__traceback__)
    fname, line_no, func, _ = tb[-1]

    detail = str(exc)
    payload = {
        "code": 500,
        "path": path,
        "file": fname,
        "function": func,
        "line": line_no,
    }

    logger.error(detail, user=user_name, **payload)
    return fastapi.responses.JSONResponse(status_code=500, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: fastapi.Request,
    exc: Exception,
) -> fastapi.responses.JSONResponse:
    user_name = await user_name_from_request(request)
    path = f"[{request.method.upper()}]"
    path += f" {request.url.path.removeprefix('/api')}"

    tb = traceback.extract_tb(exc.__traceback__)
    fname, line_no, func, _ = tb[-1]

    logger.error(f"{path}: UNHANDLED EXCEPTION", user=user_name)
    logger.error(exc)
    return fastapi.responses.JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "detail": "Internal server error",
            "traceback": f"{exc}",
            "path": path,
            "file": fname,
            "function": func,
            "line": line_no,
        },
    )


#
# GraphQL
#

app.include_router(
    graphql_router,
    prefix="/graphql",
    tags=["GraphQL"],
    include_in_schema=False,
)


@app.get("/graphiql", include_in_schema=False)
def explorer() -> fastapi.responses.HTMLResponse:
    page = pathlib.Path("static/graphiql.html").read_text()
    page = page.replace("{{ SUBSCRIPTION_ENABLED }}", "false")  # TODO
    return fastapi.responses.HTMLResponse(page, 200)


#
# Websocket
#

messaging = Messaging()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    client = await messaging.join(websocket)
    if client is None:
        return
    try:
        while True:
            message = await client.receive()
            if message is None:
                continue

            if message["topic"] == "auth":
                await client.authorize(
                    message.get("token"),
                    topics=message.get("subscribe", []),
                    project=message.get("project"),
                )
    except WebSocketDisconnect:
        try:
            del messaging.clients[client.id]
        except KeyError:
            pass


#
# REST endpoints
#


def init_api(target_app: fastapi.FastAPI, plugin_dir: str = "api") -> None:
    """Register API modules to the server"""

    sys.path.insert(0, plugin_dir)
    for module_name in sorted(os.listdir(plugin_dir)):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            log_traceback(f"Unable to initialize {module_name}")
            continue

        if not hasattr(module, "router"):
            logger.debug(f"API plug-in '{module_name}' has no router")
            continue

        target_app.include_router(module.router, prefix="/api")

    # Use endpoints function names as operation_ids
    for route in app.routes:
        if isinstance(route, fastapi.routing.APIRoute):
            if route.operation_id is None:
                route.operation_id = route.name


def init_addon_endpoints(target_app: fastapi.FastAPI) -> None:
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


def init_addon_static(target_app: fastapi.FastAPI) -> None:
    """Serve static files for addon frontends."""

    target_app.include_router(addon_static_router)


def init_global_staic(target_app: fastapi.FastAPI) -> None:
    STATIC_DIR = "/storage/static"
    try:
        os.makedirs(STATIC_DIR, exist_ok=True)
    except Exception as e:
        logger.warning(f"Unable to create {STATIC_DIR}: {e}")
        return
    target_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# API must be initialized here
# Because addons, which are initialized later
# may need access to classes initialized from the API (such as Attributes)
init_global_staic(app)
init_api(app, ayonconfig.api_modules_dir)

#
# Start up
#


async def load_access_groups() -> None:
    """Load access groups from the database."""
    from ayon_server.access.access_groups import AccessGroups

    await AccessGroups.load()


@app.on_event("startup")
async def startup_event() -> None:
    """Startup event.

    This is called after the server is started and:
        - initializes background workers
        - initializes redis2websocket bridge
        - connects to the database
        - loads access groups
    """

    # Save the process PID
    with open("/var/run/ayon.pid", "w") as f:
        f.write(str(os.getpid()))

    # Connect to the database and load stuff

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


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Shutdown event."""
    logger.info("Server is shutting down")

    await background_workers.shutdown()
    await messaging.shutdown()
    await Postgres.shutdown()
    logger.info("Server stopped", handlers=None)
