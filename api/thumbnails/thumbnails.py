from typing import Any, Literal

import aiocache
from fastapi import APIRouter, Query, Request, Response

from ayon_server.api.dependencies import (
    CurrentUser,
    FolderID,
    ProjectName,
    TaskID,
    ThumbnailContentType,
    ThumbnailID,
    VersionID,
    WorkfileID,
)
from ayon_server.api.responses import EmptyResponse
from ayon_server.entities.folder import FolderEntity
from ayon_server.entities.task import TaskEntity
from ayon_server.entities.version import VersionEntity
from ayon_server.entities.workfile import WorkfileEntity
from ayon_server.exceptions import (
    AyonException,
    ForbiddenException,
    NotFoundException,
)
from ayon_server.files import Storages
from ayon_server.helpers.thumbnails import get_fake_thumbnail, store_thumbnail
from ayon_server.lib.postgres import Postgres
from ayon_server.types import Field, OPModel
from ayon_server.utils import EntityID
from nxtools import logging

#
# Router
#

router = APIRouter(
    tags=["Thumbnails"],
)

#
# Common
#

PlaceholderOption = Literal["empty", "none"]


async def body_from_request(request: Request) -> bytes:
    result = b""
    async for chunk in request.stream():
        result += chunk
    logging.debug(f"Received thumbnail payload of {len(result)} bytes")
    return result


def get_fake_thumbnail_response() -> Response:
    """Generate a "fake thumbnail" response.

    This function creates a FastAPI Response object containing an
    1x1 transparent png and appropriate headers.
    """
    response = Response(status_code=203, content=get_fake_thumbnail())
    response.headers["Content-Type"] = "image/png"
    response.headers["Cache-Control"] = f"max-age={30}"
    return response


async def retrieve_thumbnail(
    project_name: str,
    thumbnail_id: str | None,
    placeholder: PlaceholderOption = "none",
    original: bool = False,
) -> Response:
    query = f"SELECT * FROM project_{project_name}.thumbnails WHERE id = $1"
    if thumbnail_id is not None:
        try:
            res = await Postgres.fetch(query, thumbnail_id)
        except Postgres.UndefinedTableError:
            pass  # project does not exist
        else:
            if res:
                payload = None
                if original:
                    storage = await Storages.project(project_name)
                    try:
                        payload = await storage.get_thumbnail(thumbnail_id)
                    except FileNotFoundError:
                        pass

                record = res[0]
                payload = payload or record["data"]
                return Response(
                    media_type=record["mime"],
                    status_code=200,
                    content=payload,
                    headers={
                        "X-Thumbnail-Id": thumbnail_id,
                        "X-Thumbnail-Time": str(record.get("created_at", 0)),
                        "Cache-Control": f"max-age={60}",
                    },
                )

    if placeholder == "empty":
        return get_fake_thumbnail_response()

    raise NotFoundException("Thumbnail not found")


#
# Direct thumbnail access
#


class CreateThumbnailResponseModel(OPModel):
    id: str = Field(..., title="Thumbnail ID", example="a1f2b3c4d5e6f7g8h9i0")


@router.post("/projects/{project_name}/thumbnails")
async def create_thumbnail(
    request: Request,
    user: CurrentUser,
    project_name: ProjectName,
    content_type: ThumbnailContentType,
) -> CreateThumbnailResponseModel:
    """Create a thumbnail.

    This endpoint is used to create a thumbnail not associated with any entity.
    Returns the ID of the created thumbnail. which can be used to assign it to
    an entity.
    """
    thumbnail_id = EntityID.create()
    payload = await body_from_request(request)
    await store_thumbnail(
        project_name,
        thumbnail_id,
        payload,
        mime=content_type,
        user_name=user.name,
    )
    return CreateThumbnailResponseModel(id=thumbnail_id)


@router.put(
    "/projects/{project_name}/thumbnails/{thumbnail_id}",
    status_code=204,
)
async def update_thumbnail(
    request: Request,
    user: CurrentUser,
    project_name: ProjectName,
    thumbnail_id: ThumbnailID,
    content_type: ThumbnailContentType,
) -> EmptyResponse:
    """Create or update a thumbnail with a specific ID.

    This endpoint is used to create or update a thumbnail by its ID.
    Since this is can be an security issue, this endpoint is only available
    to users with the `manager` role or higher.
    """

    if not user.is_manager:  # TBD
        raise ForbiddenException("Only managers can update arbitrary thumbnails")
    payload = await body_from_request(request)
    await store_thumbnail(
        project_name,
        thumbnail_id,
        payload,
        mime=content_type,
        user_name=user.name,
    )
    return EmptyResponse()


@router.get(
    "/projects/{project_name}/thumbnails/{thumbnail_id}",
    response_class=Response,
)
async def get_thumbnail(
    user: CurrentUser,
    project_name: ProjectName,
    thumbnail_id: ThumbnailID,
    placeholder: PlaceholderOption = Query("empty"),
    original: bool = Query(False),
) -> Response:
    """Get a thumbnail by its ID.

    This endpoint is used to retrieve a thumbnail by its ID.
    Since this is can be an security issue, this endpoint is only available
    to users with the `manager` role or higher.
    """
    if not user.is_manager:  # TBD
        raise ForbiddenException("Only managers can access arbitrary thumbnails")

    return await retrieve_thumbnail(
        project_name, thumbnail_id, placeholder=placeholder, original=original
    )


#
# Folder endpoints
#


@router.post("/projects/{project_name}/folders/{folder_id}/thumbnail", status_code=201)
async def create_folder_thumbnail(
    request: Request,
    user: CurrentUser,
    project_name: ProjectName,
    folder_id: FolderID,
    content_type: ThumbnailContentType,
) -> CreateThumbnailResponseModel:
    """Create a new thumbnail for a folder.

    Returns a thumbnail ID, which is also saved into the entity
    database record.
    """
    payload = await body_from_request(request)
    folder = await FolderEntity.load(project_name, folder_id)
    await folder.ensure_update_access(user, thumbnail_only=True)

    thumbnail_id = EntityID.create()
    await store_thumbnail(
        project_name=project_name,
        thumbnail_id=thumbnail_id,
        mime=content_type,
        payload=payload,
        user_name=user.name,
    )
    folder.thumbnail_id = thumbnail_id
    await folder.save()
    return CreateThumbnailResponseModel(id=thumbnail_id)


@router.get("/projects/{project_name}/folders/{folder_id}/thumbnail")
async def get_folder_thumbnail(
    user: CurrentUser,
    project_name: ProjectName,
    folder_id: FolderID,
    placeholder: PlaceholderOption = Query("empty"),
    original: bool = Query(False),
) -> Response:
    try:
        folder = await FolderEntity.load(project_name, folder_id)
        await folder.ensure_read_access(user)
    except AyonException as e:
        if placeholder == "empty":
            return get_fake_thumbnail_response()
        raise e

    return await retrieve_thumbnail(
        project_name, folder.thumbnail_id, placeholder=placeholder, original=original
    )


#
# Versions endpoints
#


@router.post(
    "/projects/{project_name}/versions/{version_id}/thumbnail", status_code=201
)
async def create_version_thumbnail(
    request: Request,
    user: CurrentUser,
    project_name: ProjectName,
    version_id: VersionID,
    content_type: ThumbnailContentType,
) -> CreateThumbnailResponseModel:
    payload = await body_from_request(request)
    version = await VersionEntity.load(project_name, version_id)
    await version.ensure_update_access(user)

    thumbnail_id = EntityID.create()
    await store_thumbnail(
        project_name=project_name,
        thumbnail_id=thumbnail_id,
        payload=payload,
        mime=content_type,
        user_name=user.name,
    )
    version.thumbnail_id = thumbnail_id
    await version.save()
    return CreateThumbnailResponseModel(id=thumbnail_id)


@router.get("/projects/{project_name}/versions/{version_id}/thumbnail")
async def get_version_thumbnail(
    user: CurrentUser,
    project_name: ProjectName,
    version_id: VersionID,
    placeholder: PlaceholderOption = Query("empty"),
    original: bool = Query(False),
) -> Response:
    try:
        version = await VersionEntity.load(project_name, version_id)
        await version.ensure_read_access(user)
    except AyonException as e:
        if placeholder == "empty":
            return get_fake_thumbnail_response()
        raise e
    return await retrieve_thumbnail(
        project_name, version.thumbnail_id, placeholder=placeholder, original=original
    )


#
# Workfile endpoints
#


@router.post(
    "/projects/{project_name}/workfiles/{workfile_id}/thumbnail", status_code=201
)
async def create_workfile_thumbnail(
    request: Request,
    user: CurrentUser,
    project_name: ProjectName,
    workfile_id: WorkfileID,
    content_type: ThumbnailContentType,
) -> CreateThumbnailResponseModel:
    payload = await body_from_request(request)
    workfile = await WorkfileEntity.load(project_name, workfile_id)
    await workfile.ensure_update_access(user)

    thumbnail_id = EntityID.create()
    await store_thumbnail(
        project_name=project_name,
        thumbnail_id=thumbnail_id,
        payload=payload,
        mime=content_type,
        user_name=user.name,
    )
    workfile.thumbnail_id = thumbnail_id
    await workfile.save()
    return CreateThumbnailResponseModel(id=thumbnail_id)


@router.get("/projects/{project_name}/workfiles/{workfile_id}/thumbnail")
async def get_workfile_thumbnail(
    user: CurrentUser,
    project_name: ProjectName,
    workfile_id: WorkfileID,
    placeholder: PlaceholderOption = Query("empty"),
    original: bool = Query(False),
) -> Response:
    try:
        workfile = await WorkfileEntity.load(project_name, workfile_id)
        await workfile.ensure_read_access(user)
    except AyonException:
        if placeholder == "empty":
            return get_fake_thumbnail_response()
        else:
            raise NotFoundException("Workfile not found")
    return await retrieve_thumbnail(
        project_name, workfile.thumbnail_id, placeholder=placeholder, original=original
    )


#
# Task endpoints
#


@aiocache.cached(ttl=240)
async def get_version_thumbnail_id_for_task(
    project_name: str,
    task_id: str,
    task_updated_at: Any,
) -> str | None:
    _ = task_updated_at
    query = f"""
        SELECT v.thumbnail_id
        FROM project_{project_name}.versions v
        WHERE v.task_id = $1
        AND v.thumbnail_id IS NOT NULL
        ORDER BY v.updated_at DESC
        LIMIT 1
    """
    async for row in Postgres.iterate(query, task_id):
        return row["thumbnail_id"]
    return None


@router.post("/projects/{project_name}/tasks/{task_id}/thumbnail", status_code=201)
async def create_task_thumbnail(
    request: Request,
    user: CurrentUser,
    project_name: ProjectName,
    task_id: TaskID,
    content_type: ThumbnailContentType,
) -> CreateThumbnailResponseModel:
    payload = await body_from_request(request)
    task = await TaskEntity.load(project_name, task_id)
    await task.ensure_update_access(user)

    thumbnail_id = EntityID.create()
    await store_thumbnail(
        project_name=project_name,
        thumbnail_id=thumbnail_id,
        payload=payload,
        mime=content_type,
        user_name=user.name,
    )
    task.thumbnail_id = thumbnail_id
    await task.save()
    return CreateThumbnailResponseModel(id=thumbnail_id)


@router.get("/projects/{project_name}/tasks/{task_id}/thumbnail")
async def get_task_thumbnail(
    user: CurrentUser,
    project_name: ProjectName,
    task_id: TaskID,
    placeholder: PlaceholderOption = Query("empty"),
    original: bool = Query(False),
) -> Response:
    try:
        task = await TaskEntity.load(project_name, task_id)
        await task.ensure_read_access(user)
    except AyonException:
        if placeholder == "empty":
            return get_fake_thumbnail_response()
        else:
            raise NotFoundException("Task not found")

    if task.thumbnail_id is None:
        thumbnail_id = await get_version_thumbnail_id_for_task(
            project_name,
            task_id,
            task.updated_at,
        )
    else:
        thumbnail_id = task.thumbnail_id

    return await retrieve_thumbnail(
        project_name, thumbnail_id, placeholder=placeholder, original=original
    )
