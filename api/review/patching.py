from ayon_server.api.dependencies import ActivityID, CurrentUser, ProjectName, VersionID
from ayon_server.entities import VersionEntity
from ayon_server.exceptions import BadRequestException, NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel

from .router import router


class SortReviewablesRequest(OPModel):
    sort: list[str] | None = Field(
        None,
        title="Reviewables Order",
        description="List of reviewable (activity) ids in the order "
        "you want them to appear in the UI.",
        example=[
            "c197712a48ef11ef95450242ac1f0004",
            "c519a3f448ef11ef95450242ac1f0004",
            "c8edce0648ef11ef95450242ac1f0004",
        ],
    )


class UpdateReviewablesRequest(OPModel):
    label: str | None = Field(
        None,
        title="Reviewable Label",
        example="Shoulder detail",
    )


@router.patch("/versions/{version_id}/reviewables")
async def sort_version_reviewables(
    user: CurrentUser,
    project_name: ProjectName,
    version_id: VersionID,
    request: SortReviewablesRequest,
) -> None:
    """Change the order of reviewables of a given version.

    In the payload, provide a list of activity ids (reviewables)
    in the order you want them to appear in the UI.
    """

    version = await VersionEntity.load(project_name, version_id)
    await version.ensure_update_access(user)

    res = await Postgres.fetch(
        f"""
        SELECT activity_id FROM project_{project_name}.activity_feed
        WHERE reference_type = 'origin'
        AND activity_type = 'reviewable'
        AND entity_type = 'version'
        AND entity_id = $1
        """,
        version_id,
    )

    if not res:
        raise NotFoundException(detail="Version not found")

    if request.sort is not None:
        valid_ids = {row["activity_id"] for row in res}
        requested_ids = set(request.sort)

        if requested_ids != valid_ids:
            logger.debug("Saved:", valid_ids)
            logger.debug("Requested:", requested_ids)
            raise BadRequestException(detail="Invalid reviewable ids")

        async with Postgres.acquire() as conn, conn.transaction():
            for i, activity_id in enumerate(request.sort):
                await Postgres.execute(
                    f"""
                    UPDATE project_{project_name}.activities
                    SET data = data || jsonb_build_object(
                        'reviewableOrder', $1::integer
                    )
                    WHERE id = $2
                    """,
                    i,
                    activity_id,
                )

    return None


@router.patch("/versions/{version_id}/reviewables/{activity_id}")
async def update_reviewable(
    user: CurrentUser,
    project_name: ProjectName,
    version_id: VersionID,
    reviewable_id: ActivityID,
    request: UpdateReviewablesRequest,
) -> None:
    """Update an existing reviewable,

    Currently it is only possible to update the label of a reviewable.
    """

    version = await VersionEntity.load(project_name, version_id)
    await version.ensure_update_access(user)

    if request.label is not None:
        if not request.label:
            raise BadRequestException(detail="Label cannot be empty")
        if not isinstance(request.label, str):
            raise BadRequestException(detail="Label must be a string")
        if not 0 < len(request.label) <= 255:
            raise BadRequestException(
                detail="Label must be between 1 and 255 characters"
            )

        await Postgres.execute(
            f"""
            UPDATE project_{project_name}.activities
            SET data = data || jsonb_build_object(
                'reviewableLabel', $1::text
            )
            WHERE id = $2
            """,
            request.label,
            reviewable_id,
        )
