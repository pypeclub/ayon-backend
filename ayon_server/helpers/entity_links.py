from ayon_server.lib.postgres import Connection, Postgres
from ayon_server.logging import logger
from ayon_server.types import ProjectLevelEntityType


async def remove_entity_links(
    project_name: str,
    entity_type: ProjectLevelEntityType,
    entity_id: str,
    conn: Connection | None = None,
) -> None:
    """Remove all links of the given entity

    This is called when a project-level entity is deleted.
    """

    # We actually don't care about the entity type as the ID
    # is unique enough to identify the entity
    # But we use it in the log message
    _ = entity_type

    async def _delete_links(conn: Connection) -> None:
        query = f"""
            WITH deleted AS (
                DELETE FROM project_{project_name}.links
                WHERE input_id = $1 OR output_id = $1
                RETURNING *
            )
            SELECT COUNT(*) as count FROM deleted
        """
        res = await conn.fetch(query, entity_id)
        print(query, res)
        if res and res[0]["count"] > 0:
            logger.debug(
                f"Removed {res[0]['count']} links of {entity_type} {entity_id}"
            )

    if conn is not None:
        return await _delete_links(conn)

    async with Postgres.acquire() as conn, conn.transaction():
        await _delete_links(conn)


async def remove_dead_links(project_name: str) -> None:
    """Remove all links that are not connected to any entity"""

    query = f"""
        SELECT name, input_type, output_type
        FROM project_{project_name}.link_types
    """
    link_types = await Postgres.fetch(query)
    count = 0

    for link_type in link_types:
        # remove inputs
        query = f"""
            WITH deleted AS (
                DELETE FROM project_{project_name}.links
                WHERE link_type = $1
                AND NOT EXISTS (
                    SELECT 1
                    FROM project_{project_name}.{link_type['input_type']}s t
                    WHERE t.id = links.input_id
                )
                RETURNING *
            )
            SELECT COUNT(*) as count FROM deleted
        """
        res = await Postgres.fetch(query, link_type["name"])
        if res:
            count += res[0]["count"]

        # remove outputs
        query = f"""
            WITH deleted AS (
                DELETE FROM project_{project_name}.links
                WHERE link_type = $1
                AND NOT EXISTS (
                    SELECT 1
                    FROM project_{project_name}.{link_type['output_type']}s t
                    WHERE t.id = links.output_id
                )
                RETURNING *
            )
            SELECT COUNT(*) FROM deleted
        """
        res = await Postgres.fetch(query, link_type["name"])
        if res:
            count += res[0]["count"]

    if count > 0:
        logger.info(f"Removed {count} dead links")
