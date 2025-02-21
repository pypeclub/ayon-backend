from typing import Any

from ayon_server.api.dependencies import CurrentUser, EventID
from ayon_server.api.responses import EmptyResponse
from ayon_server.events import EventModel, EventStatus, EventStream
from ayon_server.events.typing import (
    DEPENDS_ON_FIELD,
    DESCRIPTION_FIELD,
    HASH_FIELD,
    ID_FIELD,
    PAYLOAD_FIELD,
    PROGRESS_FIELD,
    PROJECT_FIELD,
    RETRIES_FIELD,
    SENDER_FIELD,
    SUMMARY_FIELD,
    TOPIC_FIELD,
    USER_FIELD,
)
from ayon_server.exceptions import ForbiddenException, NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel

from .router import router

#
# Models
#

normal_user_topic_whitelist: list[str] = []

RESTARTABLE_WHITELIST = [
    "installer.install_from_url",
    "dependency_package.install_from_url",
    "addon.install_from_url",
]

USER_EVENTS = [
    "action.launcher",
]


class DispatchEventRequestModel(OPModel):
    topic: str = TOPIC_FIELD
    sender: str | None = SENDER_FIELD
    hash: str | None = HASH_FIELD
    project: str | None = PROJECT_FIELD
    depends_on: str | None = DEPENDS_ON_FIELD
    description: str | None = DESCRIPTION_FIELD
    summary: dict[str, Any] = SUMMARY_FIELD
    payload: dict[str, Any] = PAYLOAD_FIELD
    finished: bool = Field(
        True,
        title="Finished",
        description="Is event finished (one shot event)",
        example=True,
    )
    store: bool = Field(
        True,
        title="Store",
        description="Set to False for fire-and-forget events",
        example=True,
    )
    reuse: bool = Field(
        False,
        title="Reuse",
        description="Allow reusing events with the same hash",
        example=False,
    )


class UpdateEventRequestModel(OPModel):
    sender: str | None = SENDER_FIELD
    project_name: str | None = Field(
        None,
        title="Project name",
        description="Deprecated use 'project' instead",
        deprecated=True,
    )
    project: str | None = PROJECT_FIELD
    user: str | None = USER_FIELD
    status: EventStatus | None = Field(None, title="Status", example="in_progress")
    description: str | None = DESCRIPTION_FIELD
    summary: dict[str, Any] | None = Field(None, title="Summary", example={})
    payload: dict[str, Any] | None = Field(None, title="Payload", example={})
    progress: float | None = PROGRESS_FIELD
    retries: int | None = RETRIES_FIELD


class DispatchEventResponseModel(OPModel):
    id: str = ID_FIELD


#
# Endpoints
#


@router.post("/events")
async def post_event(
    request: DispatchEventRequestModel,
    user: CurrentUser,
) -> DispatchEventResponseModel:
    if not user.is_manager:
        if request.topic not in normal_user_topic_whitelist:
            raise ForbiddenException("Not allowed to update this event")

    event_id = await EventStream.dispatch(
        request.topic,
        sender=request.sender,
        hash=request.hash,
        user=user.name,
        project=request.project,
        description=request.description,
        summary=request.summary,
        payload=request.payload,
        finished=request.finished,
        store=request.store,
        reuse=request.reuse,
    )
    return DispatchEventResponseModel(id=event_id)


@router.get("/events/{event_id}")
async def get_event(user: CurrentUser, event_id: EventID) -> EventModel:
    """Get event by ID.

    Return event data with given ID. If event is not found, 404 is returned.
    """

    if user.is_guest:
        raise ForbiddenException("Guests are not allowed to get events this way")

    return await EventStream.get(event_id)


@router.patch("/events/{event_id}", status_code=204)
async def update_existing_event(
    payload: UpdateEventRequestModel,
    user: CurrentUser,
    event_id: EventID,
) -> EmptyResponse:
    """Update existing event."""

    res = await Postgres.fetch(
        "SELECT topic, user_name, status, depends_on FROM events WHERE id = $1",
        event_id,
    )
    if not res:
        raise NotFoundException("Event not found")
    ex_event = res[0]
    event_user = ex_event["user_name"]

    if payload.status and payload.status != ex_event["status"]:
        if ex_event["topic"] in USER_EVENTS:
            # User events are events that the same user who created them
            # can update the status (or admins)
            if (user.name != event_user) and not user.is_admin:
                raise ForbiddenException("Not allowed to update status of this event")

        elif not user.is_service:
            if (ex_event["depends_on"] is None) and (
                ex_event["topic"] not in RESTARTABLE_WHITELIST
            ):
                raise ForbiddenException("Not allowed to update status of this event")

    if not user.is_manager:
        if event_user == user.name:
            raise ForbiddenException("Not allowed to update this event")
        if payload.user and payload.user != user.name:
            raise ForbiddenException("Not allowed to change user of this event")

    new_user = payload.user or event_user or user.name

    if payload.project_name:
        logger.warning(
            "Patching event with projectName is deprecated. Use 'project' instead."
        )
    await EventStream.update(
        event_id,
        sender=payload.sender,
        project=payload.project_name or payload.project,
        user=new_user,
        status=payload.status,
        description=payload.description,
        summary=payload.summary,
        payload=payload.payload,
        progress=payload.progress,
        retries=payload.retries,
    )

    return EmptyResponse()


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(user: CurrentUser, event_id: EventID) -> EmptyResponse:
    """Delete event by ID."""
    if not user.is_admin:
        raise ForbiddenException("Not allowed to delete events")

    await EventStream.delete(event_id)
    return EmptyResponse()
