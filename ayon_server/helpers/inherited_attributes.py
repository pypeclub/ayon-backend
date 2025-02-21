import time
from typing import Any

from ayon_server.entities.core import attribute_library
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger


async def _rebuild_in_transaction(
    project_name: str, project_attrib: dict[str, Any], conn
):
    st_crawl = await conn.prepare(
        f"""
        SELECT h.id, h.path, f.attrib as own, e.attrib as exported
        FROM project_{project_name}.hierarchy h
        INNER JOIN project_{project_name}.folders f
        ON h.id = f.id
        LEFT JOIN project_{project_name}.exported_attributes e
        ON h.id = e.folder_id
        ORDER BY h.path ASC
        """
    )

    st_upsert = await conn.prepare(
        f"""
         INSERT INTO project_{project_name}.exported_attributes
             (folder_id, path, attrib)
         VALUES
            ($1, $2, $3)
         ON CONFLICT (folder_id)
         DO UPDATE SET attrib = EXCLUDED.attrib, path = EXCLUDED.path
         """
    )

    # path: attrib_set cache to use when returning from child to parent
    caching: dict[tuple[str, ...], dict[str, Any]] = {}

    current_attrib_set: dict[str, Any] = {}
    buff: list[tuple[str, str, dict[str, Any]]] = []

    async for record in st_crawl.cursor():
        path_elements = tuple(record["path"].split("/"))
        if len(path_elements) == 1:
            current_attrib_set = project_attrib.copy()

        elif path_elements[:-1] in caching:
            current_attrib_set = caching[path_elements[:-1]]

        new_attrib_set = current_attrib_set.copy()
        new_attrib_set.update(record["own"])

        caching[path_elements] = new_attrib_set

        if record["exported"] != new_attrib_set:
            buff.append(
                (
                    record["id"],
                    record["path"],
                    new_attrib_set,
                )
            )

        if len(buff) > 100:
            await st_upsert.executemany(buff)
            buff = []

        current_attrib_set = new_attrib_set

    if buff:
        await st_upsert.executemany(buff)


async def rebuild_inherited_attributes(
    project_name: str, pattr: dict[str, Any] | None = None, transaction=None
):
    """Rebuild inherited attributes for all objects in the project."""
    start = time.monotonic()

    if pattr is None:
        project_attrib = attribute_library.project_defaults
        res = await Postgres.fetch(
            "SELECT attrib FROM projects WHERE name = $1", project_name
        )
        project_attrib.update(res[0]["attrib"])
    else:
        project_attrib = pattr.copy()

    # Filter out non-inheritable and non-folder attributes
    for attr_type in attribute_library["folder"]:
        if attr_type["name"] not in project_attrib:
            continue
        if not attr_type.get("inherit", True):
            del project_attrib[attr_type["name"]]

    if (transaction is None) or transaction == Postgres:
        async with Postgres.acquire() as conn, conn.transaction():
            await _rebuild_in_transaction(project_name, project_attrib, conn)
    else:
        await _rebuild_in_transaction(project_name, project_attrib, transaction)

    elapsed = time.monotonic() - start
    logger.debug(f"Rebuilt inherited attributes for {project_name} in {elapsed:.2f}s")
