from fastapi import Response

from ayon_server.api.dependencies import CurrentUser
from ayon_server.api.system import clear_server_restart_required, require_server_restart
from ayon_server.events import EventStream
from ayon_server.exceptions import ForbiddenException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel

from . import frontend_modules, info, metrics, secrets, sites
from .router import router

assert info
assert metrics
assert secrets
assert sites
assert frontend_modules


@router.post("/system/restart", response_class=Response, tags=["System"])
async def request_server_restart(user: CurrentUser):
    if not user.is_manager:
        raise ForbiddenException(
            "Only managers and administrators can restart the server"
        )

    if user.is_guest:
        raise ForbiddenException("Guests cannot restart the server")

    logger.info(f"{user.name} requested server restart", user=user.name)
    await EventStream.dispatch("server.restart_requested", user=user.name)
    return Response(status_code=204)


class RestartRequiredModel(OPModel):
    required: bool = Field(..., description="Whether the server requires a restart")
    reason: str | None = Field(None, description="The reason for the restart")


@router.get("/system/restartRequired", tags=["System"])
async def get_restart_required() -> RestartRequiredModel:
    """Get the server restart required flag.

    This will return whether the server requires a restart, and the reason for it.
    """

    # we ignore super new restart required events,
    # because they might not be cleared yet.

    res = await Postgres.fetch(
        """
        SELECT description FROM events
        WHERE topic='server.restart_required'
        AND created_at < now() - interval ' 2 seconds'
        """
    )
    if not res:
        return RestartRequiredModel(required=False, reason=None)

    return RestartRequiredModel(required=True, reason=res[0]["description"])


@router.post(
    "/system/restartRequired", tags=["System"], response_model_exclude_none=True
)
async def set_restart_required(
    request: RestartRequiredModel,
    user: CurrentUser,
):
    """Set the server restart required flag.

    This will notify the administrators that the server needs to be restarted.
    When the server is ready to restart, the administrator can use
    restart_server (using /api/system/restart) to trigger server.restart_requested
    event, which (captured by messaging) will trigger restart_server function
    and restart the server.

    Human-readable reason for the restart can be provided in the `reason` field.
    """
    if request.required:
        await require_server_restart(user.name)
    else:
        await clear_server_restart_required()

    if not user.is_admin:
        raise ForbiddenException("Only administrators can set server restart required")
