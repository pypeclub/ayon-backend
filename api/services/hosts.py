from datetime import datetime

from fastapi import Depends

from ayon_server.api.dependencies import NoTraces, dep_current_user
from ayon_server.entities import UserEntity
from ayon_server.exceptions import ForbiddenException
from ayon_server.lib.postgres import Postgres
from ayon_server.types import Field, OPModel

from .router import router
from .services import ServiceModel, list_services


class HostHealthModel(OPModel):
    cpu: float = Field(0, title="CPU utilization", ge=0, le=100, example=0.5)
    mem: float = Field(0, title="RAM utilization", ge=0, le=100, example=42)


class HostModel(OPModel):
    name: str = Field(..., title="Host name", example="my-host")
    last_seen: datetime = Field(
        ..., title="Last seen time", example=datetime.now().isoformat()
    )
    health: HostHealthModel = Field(
        default_factory=lambda: HostHealthModel(cpu=0, mem=0),
        title="Host health",
        example={"cpu": 0.5, "mem": 42},
    )


class HostListResponseModel(OPModel):
    hosts: list[HostModel] = Field(
        default_factory=list,
        title="Hosts",
        description="List of registered hosts",
    )


class HeartbeatRequestModel(OPModel):
    hostname: str
    health: HostHealthModel
    services: list[str] = Field(
        default_factory=list,
        title="List of running services",
        example=["ftrack-processor", "ftrack-event-server"],
    )


class HeartbeatResponseModel(OPModel):
    services: list[ServiceModel] = Field(
        default_factory=list,
        title="List of services that should be running",
    )


@router.get(
    "/hosts",
    response_model=HostListResponseModel,
    tags=["Services"],
)
async def list_hosts(user: UserEntity = Depends(dep_current_user)):
    """Return a list of all hosts.

    A host is an instance of Ayon Service Host (ASH) that is capable of
    running addon services. A host record in the database is created
    automatically when the host sends a heartbeat to the API.
    """
    return HostListResponseModel(
        hosts=[
            HostModel(**row) async for row in Postgres.iterate("SELECT * FROM hosts")
        ]
    )


@router.post(
    "/hosts/heartbeat",
    response_model=HeartbeatResponseModel,
    tags=["Services"],
    dependencies=[NoTraces],
)
async def host_heartbeat(
    payload: HeartbeatRequestModel,
    user: UserEntity = Depends(dep_current_user),
):
    """Send a heartbeat from a host.

    This endpoint is called by ASH to send a heartbeat to the API. The
    heartbeat contains information about the host, its health and the
    state of its services
    """

    if not user.is_service:
        raise ForbiddenException("Only services have hearts to beat")

    now = datetime.now()

    async with Postgres.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO hosts (name, last_seen, health)
                VALUES ($1, $2, $3)
                ON CONFLICT (name)
                DO UPDATE SET
                    last_seen = $2,
                    health = $3
                """,
                payload.hostname,
                now,
                payload.health.dict(),
            )

            await conn.execute(
                """
                UPDATE services SET
                is_running = (name = ANY($1::VARCHAR[]))::BOOL,
                last_seen = $2
                WHERE hostname = $3
                """,
                payload.services,
                now,
                payload.hostname,
            )

    all_services = (await list_services(user=user)).services
    services = [
        service
        for service in all_services
        if service.hostname == payload.hostname and service.should_run
    ]

    return HeartbeatResponseModel(services=services)
