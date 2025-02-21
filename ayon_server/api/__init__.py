# The following functions fix the deprecated import method
# in SiteSync addon 1.0.2 and below. That fixes crashes of
# SiteSync in Ayon 1.4.0
#
# They have been added in 1.4.1 and should be removed in 1.5.0
# e.g.: empty this file completely in 1.5.0

from typing import Annotated, Any

from fastapi import Cookie, Depends, Header, Path, Query, Request

from ayon_server.types import API_KEY_REGEX, PROJECT_NAME_REGEX, USER_NAME_REGEX
from ayon_server.utils import EntityID, parse_access_token, parse_api_key
from nxtools import logging


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


async def dep_current_user(
    request: Request,
    x_as_user: str | None = Header(
        None,
        regex=USER_NAME_REGEX,
        include_in_schema=False,
    ),
    x_api_key: str | None = Header(None, regex=API_KEY_REGEX, include_in_schema=False),
    access_token: str | None = Depends(dep_access_token),
    api_key: str | None = Depends(dep_api_key),
) -> Any:
    from .dependencies import dep_current_user

    logging.warning("Using deprecated dep_current_user")
    return await dep_current_user(request, x_as_user, x_api_key, access_token, api_key)


async def dep_project_name(
    project_name: str = Path(..., title="Project name", regex=PROJECT_NAME_REGEX),
):
    from .dependencies import dep_project_name

    logging.warning("Using deprecated dep_project_name")
    return await dep_project_name(project_name)


async def dep_representation_id(
    representation_id: str = Path(..., title="Version ID", **EntityID.META),
) -> str:
    """Validate and return a representation id specified in an endpoint path."""
    logging.warning("Using deprecated dep_representation_id")
    return representation_id
