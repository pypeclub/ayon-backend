import time
from collections import defaultdict
from typing import Any

from ayon_server.auth.session import Session
from ayon_server.entities import ProjectEntity, UserEntity
from ayon_server.entities.models.submodels import LinkTypeModel
from ayon_server.events import EventStream
from ayon_server.lib.postgres import Postgres
from ayon_server.settings.anatomy import Anatomy
from nxtools import logging


def anatomy_to_project_data(anatomy: Anatomy) -> dict[str, Any]:
    """Convert anatomy to project data."""
    task_types = [t.dict() for t in anatomy.task_types]
    folder_types = [t.dict() for t in anatomy.folder_types]
    statuses = [t.dict() for t in anatomy.statuses]
    tags = [t.dict() for t in anatomy.tags]

    config: dict[str, Any] = {}
    config["roots"] = {}
    for root in anatomy.roots:
        config["roots"][root.name] = {
            "windows": root.windows,
            "linux": root.linux,
            "darwin": root.darwin,
        }

    config["templates"] = {
        "common": {
            "version_padding": anatomy.templates.version_padding,
            "version": anatomy.templates.version,
            "frame_padding": anatomy.templates.frame_padding,
            "frame": anatomy.templates.frame,
        }
    }

    templates = anatomy.templates.dict()
    for template_type in (
        "work",
        "publish",
        "hero",
        "delivery",
        "others",
        "staging",
    ):
        template_group = templates.get(template_type, [])
        if not template_group:
            continue
        config["templates"][template_type] = {}
        for template in template_group:
            config["templates"][template_type][template["name"]] = {
                k: template[k] for k in template.keys() if k != "name"
            }

    link_types: list[LinkTypeModel] = []
    for link_type in anatomy.link_types:
        name = f"{link_type.link_type}|{link_type.input_type}|{link_type.output_type}"
        data = {"color": link_type.color, "style": link_type.style}
        link_types.append(
            LinkTypeModel(
                name=name,
                link_type=link_type.link_type,
                input_type=link_type.input_type,
                output_type=link_type.output_type,
                data=data,
            )
        )

    result = {
        "task_types": task_types,
        "folder_types": folder_types,
        "link_types": link_types,
        "statuses": statuses,
        "tags": tags,
        "attrib": anatomy.attributes.dict(),
        "config": config,
    }

    return result


async def assign_default_users_to_project(project_name: str, conn) -> None:
    """Assign a project to all users with default access groups"""

    # NOTE: we need to use explicit public here, because the
    # previous statement in the transaction scopes the transaction
    # to the project schema.

    query = """
        SELECT u.* FROM public.users AS u
        WHERE jsonb_array_length(data->'defaultAccessGroups')::boolean
        AND active
        FOR UPDATE OF u
    """

    users = await conn.fetch(query)
    if not users:
        return

    sessions = defaultdict(list)
    async for session in Session.list():
        # querying sessions for all users is not efficient
        # so we will just load all active sessions and work with them
        user_name = session.user.name
        sessions[user_name].append(session.token)

    for row in users:
        user = UserEntity.from_record(row)

        if user.is_manager:
            # we don't need to assign projects to managers and above
            # as they have access to all projects
            continue

        access_groups = user.data.get("accessGroups", {})
        access_groups[project_name] = user.data["defaultAccessGroups"]
        user.data["accessGroups"] = access_groups
        await user.save(transaction=conn, run_hooks=False)

        for token in sessions[user.name]:
            await Session.update(token, user)

        # TODO: consider dispatching an event with this information as
        # it could be used to notify the user.
        logging.debug(f"Added user {row['name']} to project {project_name}")


async def create_project_from_anatomy(
    name: str,
    code: str,
    anatomy: Anatomy,
    *,
    library: bool = False,
    user_name: str | None = None,
    data: dict[str, Any] | None = None,
    assign_users: bool = True,
) -> None:
    """Deploy a project.

    Create a new project with the given name and code, and deploy the
    given anatomy to it, assign the project to all users with
    defaultAccessGroups (if assign_users is True) and dispatch the
    entity.project.created event.

    This is a preffered way of creating a new project, as it will
    create all the necessary data in the database consistently.
    """

    project_data = anatomy_to_project_data(anatomy)
    if data:
        if "data" not in project_data:
            # now we don't expect anything to be in project_data.data
            # but we will keep this check for now for the future
            project_data["data"] = {}
        project_data["data"].update(data)

    project = ProjectEntity(
        payload={
            "name": name,
            "code": code,
            "library": library,
            **project_data,
        },
    )

    start_time = time.monotonic()
    async with Postgres.acquire() as conn, conn.transaction():
        await project.save(transaction=conn)
        if assign_users:
            await assign_default_users_to_project(project.name, conn)

    end_time = time.monotonic()
    logging.debug(f"Deployed project {project.name} in {end_time - start_time:.2f}s")

    await EventStream.dispatch(
        "entity.project.created",
        sender="ayon",
        project=project.name,
        user=user_name,
        description=f"Created project {project.name}",
    )
