import asyncio
import json
import sys
from typing import Any

from ayon_server.lib.postgres import Postgres
from ayon_server.logging import critical_error, log_traceback
from linker.linker import make_links


async def create_link_type(project_name: str, link_type: str) -> None:
    link_type_name, input_type, output_type = link_type.split("|")
    await Postgres.execute(
        f"""
        INSERT INTO project_{project_name}.link_types
            (name, input_type, output_type, link_type)
        VALUES
            ($1, $2, $3, $4)
        """,
        link_type,
        input_type,
        output_type,
        link_type_name,
    )


async def main() -> None:
    # Get generator config

    data = sys.stdin.read()
    if not data:
        critical_error("No data provided")

    gen_config: dict[str, Any] = {}
    try:
        gen_config = json.loads(data)
    except Exception:
        log_traceback()
        critical_error("Invalid project data provided")

    project_name: str = gen_config["name"]
    links_config: list[Any] = gen_config.get("links", [])

    if not links_config:
        return

    # Connect to the DB and ensure the project exists

    await Postgres.connect()

    res = await Postgres.execute("SELECT * FROM projects WHERE name = $1", project_name)
    if not res:
        critical_error(f"Project {project_name} not found")

    # Delete existing links and link types

    await Postgres.execute(f"DELETE FROM project_{project_name}.link_types")
    await Postgres.execute(f"DELETE FROM project_{project_name}.links")

    # Create link types

    link_types = list({link_type["link_type"] for link_type in links_config})

    for link_type in link_types:
        await create_link_type(project_name, link_type)

    for link_type_config in links_config:
        await make_links(project_name, link_type_config)


if __name__ == "__main__":
    asyncio.run(main())
