from ayon_server.entities.core.projectlevel import ProjectLevelEntity
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.utils import json_dumps, json_loads

REDIS_NS = "watchers"


async def build_watcher_list(entity: ProjectLevelEntity) -> list[str]:
    """Build watchers list for an entity, store it in redis and return it.

    This should be executed every tim the watchers list is updated,
    or not found in redis.
    """

    watchers: list[str] = []

    query = f"""
        SELECT activity_data->>'watcher' AS watcher
        FROM project_{entity.project_name}.activity_feed
        WHERE activity_type = 'watch'
        AND reference_type = 'origin'
        AND entity_type = $1
        AND entity_id = $2
        ORDER by entity_name ASC
        """

    try:
        res = await Postgres.fetch(query, entity.entity_type, entity.id)
    except Postgres.UndefinedTableError:
        logger.debug(
            "Unable to get watchers. " f"Project {entity.project_name} no longer exists"
        )
        return []

    if res:
        watchers = [row["watcher"] for row in res]

    await Redis.set(
        REDIS_NS,
        f"{entity.project_name}:{entity.entity_type}:{entity.id}",
        json_dumps(watchers),
    )
    return watchers


async def get_watcher_list(entity: ProjectLevelEntity) -> list[str]:
    """Get watchers of an entity.

    Returns a list of user names that are watching the entity.

    Look into Redis for the current watchers list, if not found,
    build it and return it.
    """

    watchers_result = await Redis.get(
        REDIS_NS, f"{entity.project_name}:{entity.entity_type}:{entity.id}"
    )
    if watchers_result is None:
        watchers = await build_watcher_list(entity)
        return watchers

    return json_loads(watchers_result)
