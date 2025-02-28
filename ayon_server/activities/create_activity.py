"""Create an activity.

This module exports just one function: create_activity, that creates
an activity record in the database for the given entity including
all necessary references to other entities and users.
"""

__all__ = ["create_activity"]

import datetime
from typing import Any

from ayon_server.activities.models import (
    ActivityReferenceModel,
    ActivityType,
)
from ayon_server.activities.parents import get_parents_from_entity
from ayon_server.activities.references import get_references_from_entity
from ayon_server.activities.utils import (
    MAX_BODY_LENGTH,
    extract_mentions,
    is_body_with_checklist,
    process_activity_files,
)
from ayon_server.activities.watchers.watcher_list import get_watcher_list
from ayon_server.entities.core import ProjectLevelEntity
from ayon_server.events.eventstream import EventStream
from ayon_server.exceptions import BadRequestException, NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.utils import create_uuid


async def create_activity(
    entity: ProjectLevelEntity,
    activity_type: ActivityType,
    body: str,
    *,
    tags: list[str] | None = None,
    files: list[str] | None = None,
    activity_id: str | None = None,
    user_name: str | None = None,
    extra_references: list[ActivityReferenceModel] | None = None,
    data: dict[str, Any] | None = None,
    timestamp: datetime.datetime | None = None,
    sender: str | None = None,
    sender_type: str | None = None,
) -> str:
    """Create an activity.

    extra_references is an optional list of references to entities and users.
    They are autopopulated based on the activity body and the current
    user if not provided.
    """

    if timestamp is None:
        timestamp = datetime.datetime.now(datetime.UTC)

    if len(body) > MAX_BODY_LENGTH:
        raise BadRequestException(f"{activity_type.capitalize()} body is too long")

    project_name: str = entity.project_name
    entity_type: str = entity.entity_type
    entity_id: str = entity.id

    data = data or {}

    # Origin object (for displaying in mentions etc)

    origin = {
        "type": entity_type,
        "id": entity_id,
        "name": entity.name,
    }
    if entity_type == "task":
        origin["subtype"] = entity.task_type  # type: ignore
    elif entity_type == "folder":
        origin["subtype"] = entity.folder_type  # type: ignore
    elif entity_type == "product":
        origin["subtype"] = entity.product_type  # type: ignore

    if hasattr(entity, "label"):
        origin["label"] = entity.label
    data["origin"] = origin

    try:
        data["parents"] = await get_parents_from_entity(entity)
    except Postgres.UndefinedTableError as e:
        raise NotFoundException(
            "Unable to get references. " f"Project {project_name} no longer exists"
        ) from e

    if activity_type == "comment" and is_body_with_checklist(body):
        data["hasChecklist"] = True

    #
    # Extract references
    #

    # Origin is always present. Activity is always created for a single entity.

    references: set[ActivityReferenceModel] = set(extra_references or [])

    references.add(
        ActivityReferenceModel(
            entity_id=entity_id,
            entity_type=entity_type,
            entity_name=None,
            reference_type="origin",
        )
    )

    if user_name:
        references.add(
            ActivityReferenceModel(
                entity_type="user",
                entity_name=user_name,
                reference_type="author",
                entity_id=None,
            )
        )
        data["author"] = user_name

    references.update(extract_mentions(body))
    if activity_type not in ["watch"]:
        # We don't need to collect additional references for watch activities
        # As they only apply to the entity itself

        # Add watchers first (as they are more important than mentions)
        watcher_list = await get_watcher_list(entity)
        for watcher in watcher_list:
            references.add(
                ActivityReferenceModel(
                    entity_type="user",
                    entity_name=watcher,
                    reference_type="watching",
                    entity_id=None,
                )
            )

        # Add related entities references
        try:
            references.update(await get_references_from_entity(entity))
        except Postgres.UndefinedTableError as e:
            raise NotFoundException(
                "Unable to get references. " f"Project {project_name} no longer exists"
            ) from e

    #
    # Create the activity
    #

    if not activity_id:
        activity_id = create_uuid()

    #
    # Add files
    #

    if files is not None:
        data["files"] = await process_activity_files(project_name, files)

    query = f"""
        INSERT INTO project_{project_name}.activities
        (id, activity_type, body, tags, data, created_at, updated_at)
        VALUES
        ($1, $2, $3, $4, $5, $6, $6)
    """

    async with Postgres.acquire() as conn, conn.transaction():
        tags = tags or []
        try:
            await conn.execute(
                query,
                activity_id,
                activity_type,
                body,
                tags,
                data,
                timestamp,
            )
        except Postgres.UndefinedTableError as e:
            raise NotFoundException(
                "Unable to create activity. " f"Project {project_name} no longer exists"
            ) from e

        if files is not None:
            try:
                await conn.execute(
                    f"""
                    UPDATE project_{project_name}.files
                    SET
                        activity_id = $1,
                        updated_at = NOW()
                    WHERE id = ANY($2)
                    """,
                    activity_id,
                    files,
                )
            except Postgres.UndefinedTableError as e:
                raise NotFoundException(
                    "Unable to update files. "
                    f"Project {project_name} no longer exists"
                ) from e

        st_ref = await conn.prepare(
            f"""
            INSERT INTO project_{project_name}.activity_references
            (
                id,
                activity_id,
                reference_type,
                entity_type,
                entity_id,
                entity_name,
                data,
                created_at,
                updated_at
            )
            VALUES
            ($1, $2, $3, $4, $5, $6, $7, $8, $8)
            ON CONFLICT (activity_id, reference_type, entity_id, entity_name)
            DO UPDATE SET data = EXCLUDED.data
        """
        )

        try:
            await st_ref.executemany(
                ref.insertable_tuple(activity_id, timestamp) for ref in references
            )
        except Postgres.UndefinedTableError as e:
            raise NotFoundException(
                "Unable to create references. "
                f"Project {project_name} no longer exists"
            ) from e

    # Notify the front-end about the new activity

    summary_references: list[dict[str, str]] = []
    for ref in references:
        if ref.entity_id:
            summary_references.append(
                {
                    "entity_id": ref.entity_id,
                    "entity_type": ref.entity_type,
                    "reference_type": ref.reference_type,
                }
            )

    summary = {
        "activity_id": activity_id,
        "activity_type": activity_type,
        "references": summary_references,
    }

    with logger.contextualize(activity_id=activity_id, activity_type=activity_type):
        await EventStream.dispatch(
            "activity.created",
            project=project_name,
            description=f"Created {activity_type} activity",
            summary=summary,
            store=False,
            user=user_name,
            sender=sender,
            sender_type=sender_type,
        )

        # Send inbox notifications

        notify_important: list[str] = []
        notify_normal: list[str] = []
        for ref in references:
            if ref.entity_type != "user":
                continue
            assert ref.entity_name is not None, "This should have been checked before"
            if ref.reference_type == "author":
                continue
            if (
                ref.reference_type in ["mention", "watching"]
                and activity_type != "status.change"
            ):
                notify_important.append(ref.entity_name)
            elif ref.entity_name not in notify_important:
                notify_normal.append(ref.entity_name)

        notify_description = body.split("\n")[0]
        if notify_important:
            await EventStream.dispatch(
                "inbox.message",
                project=project_name,
                description=notify_description,
                summary={"isImportant": True},
                recipients=notify_important,
                store=False,
                user=user_name,
            )
        if notify_normal:
            await EventStream.dispatch(
                "inbox.message",
                project=project_name,
                description=notify_description,
                summary={"isImportant": False},
                recipients=notify_normal,
                store=False,
                user=user_name,
            )

    return activity_id
