from datetime import datetime

from fastapi import Query

from ayon_server.api.dependencies import CurrentUser
from ayon_server.lib.postgres import Postgres
from ayon_server.types import Field, OPModel, Platform

from .router import router


class SiteInfo(OPModel):
    id: str = Field(..., title="Site identifier")
    platform: Platform = Field(...)
    hostname: str = Field(..., title="Machine hostname")
    version: str = Field(..., title="Ayon version")
    users: set[str] = Field(..., title="List of users")
    last_seen: datetime | None = Field(None, title="Last used timestamp")


@router.get("/system/sites", tags=["System"])
async def get_sites(
    user: CurrentUser,
    platform: Platform | None = Query(None),
    hostname: str | None = Query(None),
) -> list[SiteInfo]:
    """Get list of sites"""

    result: list[SiteInfo] = []
    query = "SELECT id, data, last_seen FROM sites"
    async for row in Postgres.iterate(query):
        site = SiteInfo(id=row["id"], last_seen=row["last_seen"], **row["data"])

        if platform and site.platform != platform:
            continue

        if hostname and site.hostname != hostname:
            continue

        if user.name not in site.users and (not user.is_manager):
            continue

        result.append(site)

    return result
