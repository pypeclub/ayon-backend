"""Request dependencies."""

import re
import time
from typing import Annotated, get_args

from fastapi import Cookie, Depends, Header, Path, Query, Request

from ayon_server.addons import AddonLibrary, BaseServerAddon
from ayon_server.auth.session import Session
from ayon_server.auth.utils import hash_password
from ayon_server.entities import UserEntity
from ayon_server.exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    UnsupportedMediaException,
)
from ayon_server.helpers.project_list import build_project_list, get_project_list
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.types import (
    API_KEY_REGEX,
    NAME_REGEX,
    PROJECT_NAME_REGEX,
    USER_NAME_REGEX,
    ProjectLevelEntityType,
)
from ayon_server.utils import (
    EntityID,
    parse_access_token,
    parse_api_key,
)


def dep_current_addon(request: Request) -> BaseServerAddon:
    path = request.url.path
    parts = path.split("/")
    try:
        addon_index = parts.index("addons")
        addon_name = parts[addon_index + 1]
        addon_version = parts[addon_index + 2]
    except (ValueError, IndexError):
        raise BadRequestException("Addon name or version missing in the URL")
    addon = AddonLibrary.addon(addon_name, addon_version)
    return addon


CurrentAddon = Annotated[BaseServerAddon, Depends(dep_current_addon)]


async def dep_access_token(
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
    token: Annotated[str | None, Query(include_in_schema=False)] = None,
    access_token: Annotated[
        str | None, Cookie(alias="accessToken", include_in_schema=False)
    ] = None,
) -> str | None:
    """Parse and return an access token provided in the authorisation header."""
    if token is not None:
        # try to get token from query params
        return token
    elif access_token is not None:
        # try to get token from cookies
        return access_token
    elif authorization is not None:
        # try to get token from headers
        return parse_access_token(authorization)
    else:
        return None


AccessToken = Annotated[str, Depends(dep_access_token)]


async def dep_api_key(
    authorization: str = Header(None, include_in_schema=False),
    x_api_key: str = Header(None, include_in_schema=False),
) -> str | None:
    """Parse and return an api key provided in the authorisation header."""
    api_key: str | None
    if x_api_key:
        api_key = x_api_key
    elif authorization:
        api_key = parse_api_key(authorization)
    else:
        api_key = None
    return api_key


ApiKey = Annotated[str | None, Depends(dep_api_key)]


async def dep_thumbnail_content_type(content_type: str = Header(None)) -> str:
    """Return the mime type of the thumbnail.

    Raise an `UnsupportedMediaException` if the content type is not supported.
    """
    content_type = content_type.lower()
    if content_type not in ["image/png", "image/jpeg"]:
        raise UnsupportedMediaException("Thumbnail must be in png or jpeg format")
    return content_type


ThumbnailContentType = Annotated[str, Depends(dep_thumbnail_content_type)]


async def user_from_api_key(api_key: str) -> UserEntity:
    """Return a user associated with the given API key.

    Hashed ApiKey may be stored in the database in two ways:

    1. As a string in the `apiKey` field. Original method used
       by services

    2. As an array of objects in the `apiKeys` field. Each object
       has the following fields:
        - id: identifier that allows invalidating the key
        - key: hashed api key
        - label: a human-readable label
        - preview: a preview of the key
        - created: timestamp when the key was created
        - expires(optional): timestamp when the key expires

       In this case, the key is stored in the `key` field,
       is the one we are looking for. We also need to check
       if the key is not expired.
    """
    hashed_key = hash_password(api_key)
    query = """
        SELECT * FROM users
        WHERE data->>'apiKey' = $1
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(data->'apiKeys') AS ak
            WHERE ak->>'key' = $1
        )
    """
    if not (result := await Postgres.fetch(query, hashed_key)):
        raise UnauthorizedException("Invalid API key")
    user = UserEntity.from_record(result[0])
    if user.data.get("apiKey") == hashed_key:
        return user
    for key_data in user.data.get("apiKeys", []):
        if key_data.get("key") != hashed_key:
            continue
        if key_data.get("expires") and key_data["expires"] < time.time():
            raise UnauthorizedException("API key has expired")
        return user
    raise UnauthorizedException("Invalid API key. This shouldn't happen")


async def dep_current_user(
    request: Request,
    x_as_user: str | None = Header(
        None, regex=USER_NAME_REGEX, include_in_schema=False
    ),
    x_api_key: str | None = Header(None, regex=API_KEY_REGEX, include_in_schema=False),
    access_token: str | None = Depends(dep_access_token),
    api_key: str | None = Depends(dep_api_key),
) -> UserEntity:
    """Return the currently logged-in user.

    Use `dep_access_token` to ensure a valid access token is provided
    in the authorization header and return the user associated with it.

    This is used as a dependency in the API for all endpoints that
    require authentication.

    Raise an `UnauthorizedException` if the access token is invalid,
    or the user is not permitted to access the endpoint.
    """

    if api_key := x_api_key or api_key:
        if (session_data := await Session.check(api_key, request)) is None:
            user = await user_from_api_key(api_key)
            session_data = await Session.create(user, request, token=api_key)
        session_data.is_api_key = True

    elif access_token is None:
        raise UnauthorizedException("Access token is missing")
    else:
        session_data = await Session.check(access_token, request)

    if not session_data:
        raise UnauthorizedException("Invalid access token")
    await Redis.incr("user-requests", session_data.user.name)
    user = UserEntity.from_record(session_data.user.dict())
    user.add_session(session_data)
    logger.trace(f"Authenticated as {user.name}", user=user.name)

    if x_as_user is not None and user.is_service:
        # sudo :)
        user = await UserEntity.load(x_as_user)

    endpoint = request.scope["endpoint"].__name__
    project_name = request.path_params.get("project_name", "_")
    if not user.is_manager:
        # check if the user has access to the endpoint
        # we allow _ as a project name to check global permissions
        # (namely /api/accessGroups/_)
        # but it is up to the endpoint to handle it
        if project_name != "_":
            perms = user.permissions(project_name)
            if (perms is not None) and perms.endpoints.enabled:
                if endpoint not in perms.endpoints.endpoints:
                    raise ForbiddenException(f"{endpoint} is not accessible")
    return user


CurrentUser = Annotated[UserEntity, Depends(dep_current_user)]


async def dep_current_user_optional(
    request: Request,
    access_token: AccessToken,
    api_key: ApiKey,
    x_as_user: str | None = Header(
        None, regex=USER_NAME_REGEX, include_in_schema=False
    ),
    x_api_key: str | None = Header(None, regex=API_KEY_REGEX, include_in_schema=False),
) -> UserEntity | None:
    try:
        user = await dep_current_user(
            request=request,
            x_as_user=x_as_user,
            x_api_key=x_api_key,
            access_token=access_token,
            api_key=api_key,
        )
    except UnauthorizedException:
        return None
    return user


CurrentUserOptional = Annotated[UserEntity | None, Depends(dep_current_user_optional)]


async def dep_attribute_name(
    attribute_name: str = Path(
        ...,
        title="Attribute name",
        regex=NAME_REGEX,
    ),
) -> str:
    return attribute_name


AttributeName = Annotated[str, Depends(dep_attribute_name)]


async def dep_new_project_name(
    project_name: str = Path(
        ...,
        title="Project name",
        regex=PROJECT_NAME_REGEX,
    ),
) -> str:
    """Validate and return a project name.

    This only validate the regex and does not care whether
    the project already exists, so it is used in [PUT] /projects
    request to create a new project.
    """
    return project_name


NewProjectName = Annotated[str, Depends(dep_new_project_name)]


async def dep_project_name(
    project_name: str = Path(
        ...,
        title="Project name",
        regex=PROJECT_NAME_REGEX,
    ),
) -> str:
    """Validate and return a project name specified in an endpoint path.

    This dependecy actually validates whether the project exists.
    If the name is specified using wrong letter case, it is corrected
    to match the database record.
    """

    project_list = await get_project_list()

    for pn in project_list:
        if project_name.lower() == pn.name.lower():
            return pn.name

    # try again
    project_list = await build_project_list()

    for pn in project_list:
        if project_name.lower() == pn.name.lower():
            return pn.name

    raise NotFoundException(f"Project {project_name} not found")


ProjectName = Annotated[str, Depends(dep_project_name)]


async def dep_project_name_or_underscore(
    project_name: str = Path(..., title="Project name"),
) -> str:
    if project_name == "_":
        return project_name
    return await dep_project_name(project_name)


ProjectNameOrUnderscore = Annotated[str, Depends(dep_project_name_or_underscore)]


async def dep_user_name(
    user_name: str = Path(..., title="User name", regex=USER_NAME_REGEX),
) -> str:
    """Validate and return a user name specified in an endpoint path."""
    return user_name


UserName = Annotated[str, Depends(dep_user_name)]


async def dep_access_group_name(
    access_group_name: str = Path(
        ...,
        title="Access group name",
        regex=NAME_REGEX,
    ),
) -> str:
    """Validate and return an access group name specified in an endpoint path."""
    return access_group_name


AccessGroupName = Annotated[str, Depends(dep_access_group_name)]


async def dep_secret_name(
    secret_name: str = Path(
        ...,
        title="Secret name",
        regex=NAME_REGEX,
    ),
) -> str:
    """Validate and return a secret name specified in an endpoint path."""
    return secret_name


SecretName = Annotated[str, Depends(dep_secret_name)]


async def dep_path_project_level_entity_type(
    entity_type: str = Path(...),
) -> ProjectLevelEntityType:
    """Validate and return a project level entity type specified in an endpoint path."""
    entity_type = entity_type.rstrip("s")
    if entity_type not in get_args(ProjectLevelEntityType):
        raise ValueError(f"Invalid entity type: {entity_type}")
    return entity_type  # type: ignore


PathProjectLevelEntityType = Annotated[
    ProjectLevelEntityType, Depends(dep_path_project_level_entity_type)
]


async def dep_path_entity_id(
    entity_id: str = Path(..., title="Entity ID", **EntityID.META),
) -> str:
    """Validate and return an entity id specified in an endpoint path."""
    return entity_id


PathEntityID = Annotated[str, Depends(dep_path_entity_id)]


async def dep_folder_id(
    folder_id: str = Path(..., title="Folder ID", **EntityID.META),
) -> str:
    """Validate and return a folder id specified in an endpoint path."""
    return folder_id


FolderID = Annotated[str, Depends(dep_folder_id)]


async def dep_product_id(
    product_id: str = Path(..., title="Product ID", **EntityID.META),
) -> str:
    """Validate and return a product id specified in an endpoint path."""
    return product_id


ProductID = Annotated[str, Depends(dep_product_id)]


async def dep_version_id(
    version_id: str = Path(..., title="Version ID", **EntityID.META),
) -> str:
    """Validate and return  a version id specified in an endpoint path."""
    return version_id


VersionID = Annotated[str, Depends(dep_version_id)]


async def dep_representation_id(
    representation_id: str = Path(..., title="Version ID", **EntityID.META),
) -> str:
    """Validate and return a representation id specified in an endpoint path."""
    return representation_id


RepresentationID = Annotated[str, Depends(dep_representation_id)]


async def dep_task_id(
    task_id: str = Path(..., title="Task ID", **EntityID.META),
) -> str:
    """Validate and return a task id specified in an endpoint path."""
    return task_id


TaskID = Annotated[str, Depends(dep_task_id)]


async def dep_workfile_id(
    workfile_id: str = Path(..., title="Workfile ID", **EntityID.META),
) -> str:
    """Validate and return a workfile id specified in an endpoint path."""
    return workfile_id


WorkfileID = Annotated[str, Depends(dep_workfile_id)]


async def dep_thumbnail_id(
    thumbnail_id: str = Path(..., title="Thumbnail ID", **EntityID.META),
) -> str:
    """Validate and return a thumbnail id specified in an endpoint path."""
    return thumbnail_id


ThumbnailID = Annotated[str, Depends(dep_thumbnail_id)]


async def dep_event_id(
    event_id: str = Path(..., title="Event ID", **EntityID.META),
) -> str:
    """Validate and return a event id specified in an endpoint path."""
    return event_id


EventID = Annotated[str, Depends(dep_event_id)]


async def dep_link_id(
    link_id: str = Path(..., title="Link ID", **EntityID.META),
) -> str:
    """Validate and return a link id specified in an endpoint path."""
    return link_id


LinkID = Annotated[str, Depends(dep_link_id)]


async def dep_activity_id(
    activity_id: str = Path(..., title="Activity ID", **EntityID.META),
) -> str:
    """Validate and return an activity id specified in an endpoint path."""
    return activity_id


ActivityID = Annotated[str, Depends(dep_activity_id)]


async def dep_file_id(
    file_id: str = Path(..., title="File ID", **EntityID.META),
) -> str:
    """Validate and return an file id specified in an endpoint path."""
    return file_id


FileID = Annotated[str, Depends(dep_file_id)]


async def dep_link_type(
    link_type: str = Path(..., title="Link Type"),
) -> tuple[str, str, str]:
    """Validate and return a link type specified in an endpoint path.

    Return type is a tuple of (link_type, input_type, output_type)
    """
    try:
        name, input_type, output_type = link_type.split("|")
    except ValueError:
        raise BadRequestException(
            "Link type must be in the format 'name|input_type|output_type'"
        ) from None

    if input_type not in ["folder", "product", "version", "representation", "task"]:
        raise BadRequestException(
            "Link type input type must be one of 'folder', "
            "'product', 'version', 'representation', or 'task'"
        )
    if output_type not in ["folder", "product", "version", "representation", "task"]:
        raise BadRequestException(
            "Link type output type must be one of 'folder', "
            "'product', 'version', 'representation', or 'task'"
        )

    return (name, input_type, output_type)


LinkType = Annotated[tuple[str, str, str], Depends(dep_link_type)]

#
# Site ID
#

SITE_ID_REGEX = r"^[a-z0-9-]+$"


def validate_site_id(site_id: str | None) -> None:
    """Raise a ValueError if the site id is invalid."""

    if site_id is not None and not re.match(SITE_ID_REGEX, site_id):
        raise ValueError(f"Invalid site id: {site_id}")


async def dep_client_site_id(
    param1: str | None = Query(
        None, title="Site ID", alias="site_id", include_in_schema=False
    ),
    param2: str | None = Query(
        None, title="Site ID", alias="site", include_in_schema=False
    ),
    x_ayon_site_id: str | None = Header(
        None,
        title="Site ID",
        description=(
            "Site ID may be specified either "
            "as a query parameter (`site_id` or `site`) or in a header."
        ),
    ),
) -> str | None:
    """Validate and return a site id

    SiteID may be specified in an endpoint header or query parameter.
    This is usually used for request from the client application.
    """
    site_id = param1 or param2 or x_ayon_site_id
    validate_site_id(site_id)
    return site_id


ClientSiteID = Annotated[str | None, Depends(dep_client_site_id)]


async def dep_site_id(
    param1: str | None = Query(
        None,
        title="Site ID",
        alias="site_id",
        description=(
            "Site ID may be specified a query parameter. "
            "Both `site_id` and its's alias `site` are supported."
        ),
    ),
    param2: str | None = Query(
        None,
        title="Site ID",
        alias="site",
        include_in_schema=False,
    ),
) -> str | None:
    """Validate and return a site id specified as an query argument

    either `site_id` or `site` may be used.
    This is used for management / settings endpoints.
    """
    site_id = param1 or param2
    validate_site_id(site_id)
    return site_id


SiteID = Annotated[str | None, Depends(dep_site_id)]


async def dep_sender(
    x_sender: str | None = Header(
        None,
        title="Sender",
        regex=NAME_REGEX,
    ),
) -> str | None:
    return x_sender


Sender = Annotated[str | None, Depends(dep_sender)]


async def dep_sender_type(
    x_sender_type: str = Header(
        "api",
        title="Sender type",
        regex=NAME_REGEX,
    ),
) -> str:
    return x_sender_type


SenderType = Annotated[str | None, Depends(dep_sender_type)]
