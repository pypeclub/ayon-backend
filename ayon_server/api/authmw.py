import sys
import time

from fastapi.routing import APIRoute
from shortuuid import ShortUUID
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ayon_server.api.dependencies import NoTraces
from ayon_server.auth.session import Session
from ayon_server.auth.utils import hash_password
from ayon_server.entities import UserEntity
from ayon_server.exceptions import UnauthorizedException
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.utils.strings import parse_access_token, parse_api_key


def sprint(*args):
    print(*args, flush=True, file=sys.stderr)


def access_token_from_request(request: Request) -> str | None:
    token = request.cookies.get("accessToken")
    if not token:
        authorization = request.headers.get("Authorization")
        if authorization:
            token = parse_access_token(authorization)
    if not token:
        token = request.query_params.get("token")
    return token


async def user_from_api_key(api_key: str) -> UserEntity:
    """Return a user associated with the given API key.

    Hashed ApiKey may be stored in the database in two ways:

    1. As a string in the `apiKey` field. Original method used
       by services

    2. As an array of objects in the `apiKeys` field. Each object
       has the following fields:
        - id: identifier that allows invalidating the key
        - key: hashed api key
        - label: a human-readable label
        - preview: a preview of the key
        - created: timestamp when the key was created
        - expires(optional): timestamp when the key expires

       In this case, the key is stored in the `key` field,
       is the one we are looking for. We also need to check
       if the key is not expired.
    """
    hashed_key = hash_password(api_key)
    query = """
        SELECT * FROM users
        WHERE data->>'apiKey' = $1
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(data->'apiKeys') AS ak
            WHERE ak->>'key' = $1
        )
    """
    if not (result := await Postgres.fetch(query, hashed_key)):
        raise UnauthorizedException("Invalid API key")
    user = UserEntity.from_record(result[0])
    if user.data.get("apiKey") == hashed_key:
        return user
    for key_data in user.data.get("apiKeys", []):
        if key_data.get("key") != hashed_key:
            continue
        if key_data.get("expires") and key_data["expires"] < time.time():
            raise UnauthorizedException("API key has expired")
        return user
    raise UnauthorizedException("Invalid API key. This shouldn't happen")


async def user_from_request(request: Request) -> UserEntity:
    """Get user from request"""

    # Try loading API KEY
    # API Key may be stored as x-api-key header or as
    # authorization: apikey <key> header

    api_key = request.headers.get("x-api-key")
    if not api_key:
        authorization = request.headers.get("Authorization")
        if authorization:
            api_key = parse_api_key(authorization)

    if api_key:
        if (session_data := await Session.check(api_key, request)) is None:
            user = await user_from_api_key(api_key)
            session_data = await Session.create(user, request, token=api_key)
        session_data.is_api_key = True

    elif access_token := access_token_from_request(request):
        session_data = await Session.check(access_token, request)
    else:
        raise UnauthorizedException("Access token is missing")

    if not session_data:
        raise UnauthorizedException("Invalid access token")

    await Redis.incr("user-requests", session_data.user.name)
    user = UserEntity.from_record(session_data.user.dict())
    user.add_session(session_data)

    if (x_as_user := request.headers.get("x-as-user")) and user.is_service:
        # sudo :)
        user = await UserEntity.load(x_as_user)

    return user


def req_id():
    return ShortUUID().random(length=16)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = req_id()
        path = request.url.path
        context = {"request_id": request_id}

        with logger.contextualize(**context):
            # At this point, we don't have access to the user
            # But we want to be able to pair log messages
            # from the authentication process using request_id
            try:
                user = await user_from_request(request)
                context["user"] = user.name
                request.state.user = user
                request.state.unauthorized_reason = None
            except UnauthorizedException as e:
                request.state.user = None
                request.state.unauthorized_reason = str(e)

        with logger.contextualize(**context):
            start_time = time.perf_counter()
            response = await call_next(request)
            process_time = round(time.perf_counter() - start_time, 4)
            status_code = response.status_code

            # we don't track statics
            should_trace = path.startswith("/api")

            # TODO
            # do not trace graphql queries yet, until we can make
            # sense of them - we need to at least identify the query
            # or path.startswith("/graphql")

            # Before processing the request, we don't have access to
            # the route information, so we need to check it here
            # (that's also why we don't track the beginning of the request)

            if should_trace and (route := request.scope.get("route")):
                # We don't need to log successful requests to routes,
                # that have "NoTraces" dependencies.
                # They are usually heartbeats that pollute the logs.
                if isinstance(route, APIRoute):
                    for dependency in route.dependencies:
                        if dependency == NoTraces:
                            should_trace = False

            # We're adding 'nodb' flag here, that instructs the
            # log collect to Not store the message in the event stream.

            if should_trace:
                extras = {
                    **context,
                    "nodb": True,  # don't store in the event stream
                    # TODO: Remove?
                    # Already formated in the log message
                    # "process_time": process_time,
                    # "status_code": status_code,
                    # "method": request.method,
                    # "path": path,
                }
                f_result = f"| {status_code} in {process_time}s"
                msg = f"[{request.method}] {path} {f_result}"
                logger.trace(msg, **extras)

        return response
