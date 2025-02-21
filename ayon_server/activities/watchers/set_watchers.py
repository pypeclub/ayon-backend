from collections.abc import AsyncGenerator

from ayon_server.activities.create_activity import create_activity
from ayon_server.entities import VersionEntity
from ayon_server.entities.core.projectlevel import ProjectLevelEntity
from ayon_server.entities.user import UserEntity
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger

from .watcher_list import build_watcher_list, get_watcher_list


async def get_task_versions(
    project_name: str, task_id: str
) -> AsyncGenerator[VersionEntity, None]:
    """Get all versions of a task."""

    query = f"""
        SELECT *
        FROM project_{project_name}.versions
        WHERE task_id = $1
        ORDER BY version DESC
        """
    async for row in Postgres.iterate(query, task_id):
        yield VersionEntity(project_name=project_name, payload=dict(row), exists=True)


async def set_watchers(
    entity: ProjectLevelEntity,
    watchers: list[str],  # list of user names
    user: UserEntity | str | None = None,  # who is setting the watchers
    *,
    sender: str | None = None,
    sender_type: str | None = None,
    commit: bool = True,
) -> None:
    """Set watchers of an entity.

    Compare received watchers list with the current watchers list,
    delete users that are not in the received list, and add users that are
    not in the current list.
    """

    user_name = None
    if isinstance(user, UserEntity):
        user_name = user.name
    elif isinstance(user, str):
        user_name = user

    project_name = entity.project_name
    original_watchers = await get_watcher_list(entity)

    unwatchers = [w for w in original_watchers if w not in watchers]
    new_watchers = [w for w in watchers if w not in original_watchers]

    # We are just assuming here that no one dared to link any files to this
    # and it is safe to just delete the activities without checking for files.

    if unwatchers:
        query = f"""
            WITH activities_to_delete AS (
                SELECT activity_id FROM project_{project_name}.activity_feed
                WHERE activity_type = 'watch'
                AND reference_type = 'origin'
                AND entity_type = $1
                AND entity_id = $2
                AND COALESCE(activity_data->>'watcher', '')::TEXT = ANY ($3)
            )
            DELETE FROM project_{project_name}.activities
            WHERE id IN (SELECT activity_id FROM activities_to_delete)
        """

        try:
            await Postgres.execute(query, entity.entity_type, entity.id, unwatchers)
        except Postgres.UndefinedTableError:
            logger.debug(
                "Unable to delete watchers. "
                f"Project {entity.project_name} no longer exists"
            )
            return

    # Add new watchers

    for watcher in new_watchers:
        await create_activity(
            entity=entity,
            activity_type="watch",
            body="",
            user_name=user_name,
            sender=sender,
            sender_type=sender_type,
            data={"watcher": watcher},
        )

    # Watch / unwatch all versions of a task

    if entity.entity_type == "task":
        async for version in get_task_versions(project_name, entity.id):
            original_watchers = await get_watcher_list(version)
            new_watchers = [w for w in watchers if w not in original_watchers]
            unwatchers = [w for w in original_watchers if w not in watchers]
            query = f"""
                WITH activities_to_delete AS (
                    SELECT activity_id FROM project_{project_name}.activity_feed
                    WHERE activity_type = 'watch'
                    AND reference_type = 'origin'
                    AND entity_type = $1
                    AND entity_id = $2
                    AND COALESCE(activity_data->>'watcher', '')::TEXT = ANY ($3)
                )
                DELETE FROM project_{project_name}.activities
                WHERE id IN (SELECT activity_id FROM activities_to_delete)
            """
            await Postgres.execute(query, "version", version.id, unwatchers)

            for watcher in new_watchers:
                await create_activity(
                    entity=version,
                    activity_type="watch",
                    body="",
                    user_name=user_name,
                    sender=sender,
                    data={"watcher": watcher},
                )
            await build_watcher_list(version)

    await build_watcher_list(entity)


async def ensure_watching(entity: ProjectLevelEntity, user: UserEntity | str) -> None:
    """Ensure that a user is watching an entity.

    This should be called when a user first adds a comment or a reviewable.
    """

    user_name = user.name if isinstance(user, UserEntity) else user

    try:
        watchers = await get_watcher_list(entity)
    except Postgres.UndefinedTableError:
        logger.debug(f"Unable to set watchers. Entity {entity} no longer exists")
        return

    if user_name not in watchers:
        logger.debug(f"Adding {user_name} to watchers of {entity}")
        watchers.append(user_name)
        await set_watchers(entity, watchers, user=user)


async def ensure_not_watching(
    entity: ProjectLevelEntity, user: UserEntity | str
) -> None:
    """Ensure that a user is not watching an entity.

    This should be called when a user is unassigned from a task.
    """

    user_name = user.name if isinstance(user, UserEntity) else user

    try:
        watchers = await get_watcher_list(entity)
    except Postgres.UndefinedTableError:
        logger.debug(f"Unable to set watchers. Entity {entity} no longer exists")
        return

    logger.debug(f"Watchers: {watchers}")

    if user_name in watchers:
        logger.debug(f"Removing {user_name} from watchers of {entity}")
        watchers.remove(user_name)
        await set_watchers(entity, watchers, user=user)
