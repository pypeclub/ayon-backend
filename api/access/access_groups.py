import copy

from fastapi import BackgroundTasks, Body

from ayon_server.access.access_groups import AccessGroups
from ayon_server.access.permissions import Permissions
from ayon_server.api.dependencies import (
    AccessGroupName,
    CurrentUser,
    ProjectNameOrUnderscore,
)
from ayon_server.api.responses import EmptyResponse
from ayon_server.exceptions import (
    ConstraintViolationException,
    ForbiddenException,
    NotFoundException,
)
from ayon_server.lib.postgres import Postgres
from ayon_server.settings.postprocess import postprocess_settings_schema
from ayon_server.types import Field, OPModel
from nxtools import log_traceback

from .router import router


async def clean_up_user_access_groups() -> None:
    """Remove deleted access groups from user records"""

    async with Postgres.acquire() as conn, conn.transaction():
        res = await conn.fetch("SELECT name FROM access_groups")
        if not res:
            return
        existing_access_groups = [row["name"] for row in res]

        query = "SELECT name, data FROM users FOR UPDATE OF users"
        user_map = await Postgres.fetch(query)
        for row in user_map:
            user_name = row["name"]
            user_data = row["data"]
            save = False

            if def_access_groups := user_data.get("defaultAccessGroups", []):
                if isinstance(def_access_groups, list):  # just in case
                    for ag in def_access_groups:
                        if ag not in existing_access_groups:
                            def_access_groups.remove(ag)
                            save = True

            if isinstance(acc_groups := user_data.get("accessGroups", {}), dict):
                for project_access_groups in acc_groups.values():
                    if isinstance(project_access_groups, list):  # just in case
                        for ag in project_access_groups:
                            if ag not in existing_access_groups:
                                project_access_groups.remove(ag)
                                save = True

            if save:
                await Postgres.execute(
                    "UPDATE users SET data = $2 WHERE name = $1",
                    user_name,
                    user_data,
                )


@router.get("/accessGroups/_schema")
async def get_access_group_schema():
    schema = copy.deepcopy(Permissions.schema())
    await postprocess_settings_schema(schema, Permissions)
    return schema


class AccessGroupObject(OPModel):
    name: str = Field(
        ...,
        description="Name of the access group",
        example="artist",
    )
    is_project_level: bool = Field(
        ...,
        description="Whether the access group is project level",
        example=False,
    )


@router.get("/accessGroups/{project_name}")
async def get_access_groups(
    user: CurrentUser,
    project_name: ProjectNameOrUnderscore,
) -> list[AccessGroupObject]:
    """Get a list of access group for a given project"""
    rdict = {}

    for ag_key, _perms in AccessGroups.access_groups.items():
        access_group_name, pname = ag_key
        if pname == "_":
            if access_group_name in rdict:
                continue
            else:
                rdict[access_group_name] = {"isProjectLevel": False}
        elif pname == project_name:
            rdict[access_group_name] = {"isProjectLevel": pname != "_"}

    result: list[AccessGroupObject] = []
    for access_group_name, data in rdict.items():
        result.append(AccessGroupObject(name=access_group_name, **data))
    result.sort(key=lambda x: x.name)
    return result


@router.get(
    "/accessGroups/{access_group_name}/{project_name}",
    response_model_exclude_none=True,
)
async def get_access_group(
    user: CurrentUser,
    access_group_name: AccessGroupName,
    project_name: ProjectNameOrUnderscore,
) -> Permissions:
    """Get an access group definition"""
    return AccessGroups.combine([access_group_name], project_name)


@router.put(
    "/accessGroups/{access_group_name}/{project_name}",
    status_code=204,
)
async def save_access_group(
    user: CurrentUser,
    access_group_name: AccessGroupName,
    project_name: ProjectNameOrUnderscore,
    data: Permissions = Body(..., description="Set of permissions"),
) -> EmptyResponse:
    """Create or update an access group.

    Use `_` as a project name to save a global access group.
    """

    if not user.is_manager:
        if project_name == "_":
            raise ForbiddenException(
                "Only managers can create or update global access groups"
            )
        user.check_permissions("project.access", project_name=project_name, write=True)

    scope = "public" if project_name == "_" else f"project_{project_name}"

    try:
        await Postgres.execute(
            f"""
            INSERT INTO {scope}.access_groups (name, data)
            VALUES ($1, $2)
            ON CONFLICT (name)
            DO UPDATE SET data = $2
            """,
            access_group_name,
            data.dict(),
        )
    except Exception:
        # TODO: which exception is raised?
        log_traceback()
        raise ConstraintViolationException(
            f"Unable to add access group {access_group_name}"
        ) from None

    await AccessGroups.load()
    # TODO: messaging: notify other instances
    return EmptyResponse()


@router.delete("/accessGroups/{access_group_name}/{project_name}", status_code=204)
async def delete_access_group(
    user: CurrentUser,
    access_group_name: AccessGroupName,
    project_name: ProjectNameOrUnderscore,
    background_tasks: BackgroundTasks,
):
    """Delete an access group"""

    if not user.is_manager:
        if project_name == "_":
            raise ForbiddenException("Only managers can modify global access groups")
        user.check_permissions("project.access", project_name=project_name, write=True)

    if (access_group_name, project_name) not in AccessGroups.access_groups:
        raise NotFoundException(
            f"Unable to delete access group {access_group_name}. Not found"
        )

    scope = "public" if project_name == "_" else f"project_{project_name}"

    await Postgres.execute(
        f"DELETE FROM {scope}.access_groups WHERE name = $1",
        access_group_name,
    )

    if scope == "public":
        background_tasks.add_task(clean_up_user_access_groups)

    await AccessGroups.load()
    # TODO: messaging: notify other instances

    return EmptyResponse()
