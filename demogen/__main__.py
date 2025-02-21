import asyncio
import datetime
import json
import random
import sys

from ayon_server.entities import ProjectEntity
from ayon_server.helpers.deploy_project import create_project_from_anatomy
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import critical_error, log_traceback, logger
from ayon_server.settings.anatomy import Anatomy
from ayon_server.settings.anatomy.tags import Tag
from demogen.demogen import DemoGen


def create_color() -> str:
    """Return a random color visible on dark background"""
    color = [random.randint(0, 255) for _ in range(3)]
    if sum(color) < 400:
        color = [255 - x for x in color]
    return f'#{"".join([f"{x:02x}" for x in color])}'


def generate_tags() -> list[Tag]:
    pool = """
    beauty cute funny happy sad scary sexy
    cuddly fluffy soft warm fuzzy hairy furry
    spiky sharp pointy dangerous adorable
    flabadob blip blup blip boo
    """
    names: list[str] = [r.strip() for r in pool.split() if r.strip()]
    tags: list[Tag] = []
    for name in names:
        tags.append(
            Tag(
                name=name,
                color=create_color(),
                original_name=name,
            )
        )
    return tags


def random_datetime(days_offset: int) -> datetime.datetime:
    """Return a random datetime in a given range

    Negative offset means that the date will be in the past,
    positive offset means that the date will be in the future.
    """
    now = datetime.datetime.now()
    if days_offset >= 0:
        random_offset = random.randint(0, days_offset)
    else:
        random_offset = random.randint(days_offset, 0)
    result = now + datetime.timedelta(days=random_offset)

    # make result timezone aware
    result = result.replace(tzinfo=datetime.UTC)
    return result


async def main() -> None:
    data = sys.stdin.read()
    if not data:
        critical_error("No data provided")

    try:
        project_template = json.loads(data)
    except Exception:
        log_traceback()
        critical_error("Invalid project data provided")
        raise  # unreachable, but we need to satisfy mypy

    anatomy = Anatomy()
    anatomy.tags = generate_tags()
    anatomy.attributes = ProjectEntity.model.attrib_model(  # type: ignore
        startDate=random_datetime(-30),
        endDate=random_datetime(90),
    )
    project_name = project_template["name"]
    project_hierarchy = project_template["hierarchy"]

    logger.info("Connecting to database")
    await Postgres.connect()

    logger.info("Deleting old project if exists")
    await Postgres.execute(f"DROP SCHEMA IF EXISTS project_{project_name} CASCADE")
    await Postgres.execute("DELETE FROM projects WHERE name = $1", project_name)

    await create_project_from_anatomy(
        name=project_template["name"],
        code=project_template["code"],
        anatomy=anatomy,
        data={"projectRole": "demo"},
    )

    demo = DemoGen()
    await demo.populate(project_name, project_hierarchy)


if __name__ == "__main__":
    asyncio.run(main())
