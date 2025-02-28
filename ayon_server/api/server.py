import importlib
import os
import pathlib
import sys
import traceback

import fastapi
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect

# okay. now the rest
from ayon_server.api.authmw import AuthMiddleware
from ayon_server.api.lifespan import lifespan
from ayon_server.api.messaging import messaging
from ayon_server.api.metadata import app_meta, tags_meta
from ayon_server.api.postgres_exceptions import (
    IntegrityConstraintViolationError,
    parse_postgres_exception,
)
from ayon_server.api.responses import ErrorResponse
from ayon_server.auth.session import Session
from ayon_server.background.log_collector import log_collector
from ayon_server.config import ayonconfig
from ayon_server.exceptions import AyonException
from ayon_server.graphql import router as graphql_router
from ayon_server.logging import log_traceback, logger
from ayon_server.utils import parse_access_token

# We just need the log collector to be initialized.
_ = log_collector
# But we need this. but depending on the AYON_RUN_MAINTENANCE
# environment variable, we might not run the maintenance tasks.

#
# Let's create the app
#

app = fastapi.FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url="/docs" if not ayonconfig.disable_rest_docs else None,
    openapi_tags=tags_meta,
    **app_meta,
)

app.add_middleware(AuthMiddleware)


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
