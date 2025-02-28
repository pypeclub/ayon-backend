import re
import time

from fastapi import Request

from ayon_server.api.dependencies import CurrentUser, NoTraces, Sender, SenderType
from ayon_server.api.responses import EmptyResponse
from ayon_server.events.enroll import EnrollResponseModel, enroll_job
from ayon_server.exceptions import (
    BadRequestException,
    ForbiddenException,
    ServiceUnavailableException,
)
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.sqlfilter import QueryFilter
from ayon_server.types import TOPIC_REGEX, Field, OPModel
from ayon_server.utils import hash_data

from .router import router

#
# Enroll (get a new job)
#


class EnrollRequestModel(OPModel):
    source_topic: str | list[str] = Field(
        ...,
        title="Source topic(s)",
        example="ftrack.update",
    )
    target_topic: str = Field(
        ...,
        title="Target topic",
        example="ftrack.sync_to_ayon",
        regex=TOPIC_REGEX,
    )
    sender: str | None = Field(
        None,
        title="Sender",
        example="workerservice01",
    )
    sender_type: str | None = Field(
        None,
        title="Sender type",
        example="worker",
    )
    description: str | None = Field(
        None,
        title="Description",
        description="Short, human readable description of the target event",
        example="Sync Ftrack project to Ayon",
    )
    sequential: bool = Field(
        False,
        title="Sequential",
        description="Ensure events are processed in sequential order",
        example=True,
    )
    filter: QueryFilter | None = Field(
        None, title="Filter", description="Filter source events"
    )
    max_retries: int = Field(3, title="Max retries", example=3)
    ignore_sender_types: list[str] | None = Field(
        None,
        title="Ignore sender types",
        example=["worker"],
        description="Ignore source events created by specified sender types",
    )
    ignore_older_than: int = Field(
        3,
        title="Ignore older than",
        example=3,
        description="Ignore events older than this many days. Use 0 for no limit",
    )
    sloth_mode: bool = Field(
        False,
        title="Sloth mode",
        description=(
            "Causes enroll endpoint to be really really slow. "
            "This flag is for development and testing purposes only. "
            "Never use it in production."
        ),
        example=False,
    )


def validate_source_topic(value: str) -> str:
    if not re.match(TOPIC_REGEX, value):
        raise BadRequestException(f"Invalid topic: {value}")
    return value.replace("*", "%")


# response model must be here
@router.post("/enroll", response_model=EnrollResponseModel, dependencies=[NoTraces])
async def enroll(
    request: Request,
    payload: EnrollRequestModel,
    current_user: CurrentUser,
    sender: Sender,
    sender_type: SenderType,
) -> EnrollResponseModel | EmptyResponse:
    """Enroll for a new job.

    Enroll for a new job by providing a source topic and target topic.
    Used by workers to get a new job to process. If there is no job
    available, request returns 204 (no content).

    Returns 503 (service unavailable) if the database pool is almost full.
    Processing jobs should never block user requests.

    Non-error response is returned because having nothing to do is not an error
    and we don't want to spam the logs.
    """

    def sloth(*args):
        if payload.sloth_mode:
            if not args:
                print()
            else:
                logger.debug("🦥", *args)

    if not current_user.is_service:
        raise ForbiddenException("Only services can enroll for jobs")

    # source_topic
    source_topic: str | list[str]

    if isinstance(payload.source_topic, str):
        source_topic = validate_source_topic(payload.source_topic)
    else:
        source_topic = [validate_source_topic(t) for t in payload.source_topic]

    # target_topic
    if "*" in payload.target_topic:
        raise BadRequestException("Target topic must not contain wildcards")

    # Keep DB pool size above 3

    if Postgres.get_available_connections() < 3:
        raise ServiceUnavailableException(
            f"Postgres remaining pool size: {Postgres.get_available_connections()}"
        )

    user_name = current_user.name

    ignore_older: int | None = payload.ignore_older_than
    if payload.ignore_older_than == 0:
        ignore_older = None

    if payload.ignore_sender_types is not None and not payload.ignore_sender_types:
        payload.ignore_sender_types = None

    request_hash = hash_data(payload.dict())
    sloth()
    sloth(f"Received enroll request from {payload.sender}! Hash {request_hash}")

    cached_result = await Redis.get_json("enroll", request_hash)
    if cached_result and time.time() - cached_result["timestamp"] < 600:
        if cached_result["status"] == "processing":
            sloth("Request is already running. Returning 503.")
            raise ServiceUnavailableException("Enroll request is already running")

        sloth("Request is already completed. Returning the cached result.")
        await Redis.delete("enroll", request_hash)
        if result := cached_result.get("result"):
            return EnrollResponseModel(**result)
        return EmptyResponse()  # return empty response if there is no result

    else:
        if cached_result:
            sloth("Request is already completed but cache is expired. Re-processing")
        else:
            sloth("This is a new request. Caching.")
        await Redis.set_json(
            "enroll",
            request_hash,
            {"status": "processing", "timestamp": time.time()},
            ttl=600,
        )

    if payload.sender is None and sender:
        payload.sender = sender
    if payload.sender_type is None and sender_type:
        payload.sender_type = sender_type

    try:
        res = await enroll_job(
            source_topic,
            payload.target_topic,
            sender=payload.sender,
            sender_type=payload.sender_type,
            user_name=user_name,
            description=payload.description,
            sequential=payload.sequential,
            filter=payload.filter,
            max_retries=payload.max_retries,
            ignore_older_than=ignore_older,
            ignore_sender_types=payload.ignore_sender_types,
            sloth_mode=payload.sloth_mode,
        )
    except Exception:
        # something went wrong, remove the cache
        await Redis.delete("enroll", request_hash)
        raise  # re-raise the exception

    finally:
        try:
            # Don't cache the result if an exception occurred
            # during the processing. EmptyResponse is here just
            # to make mypy happy. Actual response (triggered by
            # the exception) has already been sent to the client.
            _ = res
        except UnboundLocalError:
            return EmptyResponse()

        sloth()
        sloth(f"Enroll request {request_hash} completed")
        if await request.is_disconnected():
            sloth(f"({payload.sender}) is disconnected. Caching the result.")
            if res is None:
                r = None
            else:
                r = res.dict()

            await Redis.set_json(
                "enroll",
                request_hash,
                {"status": "done", "result": r, "timestamp": time.time()},
                ttl=60,
            )

            # no point of returning the result to the client
            # as nobody is waiting for it
            return EmptyResponse()

        else:
            sloth("Client is still connected. Returning the result and purging cache.")
            await Redis.delete("enroll", request_hash)

            if res is None:
                return EmptyResponse()
            return res
