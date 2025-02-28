import contextlib
from typing import Any
from urllib.parse import urlparse

import aiocache
from attributes.attributes import AttributeModel  # type: ignore
from fastapi import Query, Request
from pydantic import ValidationError

from ayon_server.addons import AddonLibrary, SSOOption
from ayon_server.api.dependencies import CurrentUserOptional, NoTraces
from ayon_server.config import ayonconfig
from ayon_server.config.serverconfig import get_server_config
from ayon_server.entities import UserEntity
from ayon_server.entities.core.attrib import attribute_library
from ayon_server.helpers.cloud import CloudUtils
from ayon_server.helpers.email import is_mailing_enabled
from ayon_server.info import ReleaseInfo, get_release_info, get_uptime, get_version
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import log_traceback, logger
from ayon_server.types import Field, OPModel

from .router import router
from .sites import SiteInfo


class InfoResponseModel(OPModel):
    motd: str | None = Field(
        None,
        title="Login Page Message",
        description="Instance specific message to be displayed in the login page",
        example="Hello and welcome to Ayon!",
    )
    login_page_background: str | None = Field(
        None,
        description="URL of the background image for the login page",
        example="https://i.insider.com/602ee9d81a89f20019a377c6?width=1136&format=jpeg",
    )
    login_page_brand: str | None = Field(
        None,
        title="Brand logo",
        description="URL of the brand logo for the login page",
    )
    release_info: ReleaseInfo | None = Field(
        default_factory=get_release_info,
        title="Release info",
        description="Information about the current release",
    )
    version: str = Field(
        default_factory=get_version,
        title="Ayon version",
        description="Version of the Ayon API",
    )
    uptime: float = Field(
        default_factory=get_uptime,
        title="Uptime",
        description="Time (seconds) since the server was started",
    )
    no_admin_user: bool | None = Field(
        None,
        title="No admin user",
        description="No admin user exists, display 'Create admin user' form",
    )
    onboarding: bool | None = Field(
        None,
        title="Onboarding",
    )
    password_recovery_available: bool | None = Field(None, title="Password recovery")
    user: UserEntity.model.main_model | None = Field(None, title="User information")  # type: ignore
    attributes: list[AttributeModel] | None = Field(None, title="List of attributes")

    sites: list[SiteInfo] | None = Field(None, title="List of sites")
    sso_options: list[SSOOption] | None = Field(None, title="SSO options")
    extras: str | None = Field(None)


# Ensure that an admin user exists
# This is used to determine if the 'Create admin user' form should be displayed
# We raise an exception in ensure_admin_user_exists, so the False value is not cached
# and when the admin user is created, we use the cache to avoid unnecessary queries


@aiocache.cached()
async def ensure_admin_user_exists() -> None:
    res = await Postgres.fetch(
        "SELECT name FROM users WHERE (data->'isAdmin')::boolean"
    )
    if not res:
        raise ValueError("No admin user exists")
    return None


async def admin_exists() -> bool:
    try:
        await ensure_admin_user_exists()
        return True
    except ValueError:
        return False


# Get all SSO options from the active addons


@aiocache.cached(ttl=10)
async def get_sso_options(request: Request) -> list[SSOOption]:
    referer = request.headers.get("referer")
    if referer:
        parsed_url = urlparse(referer)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    else:
        base_url = "http://localhost:5000"

    result = []
    library = AddonLibrary.getinstance()
    active_versions = await library.get_active_versions()

    for _name, definition in library.data.items():
        try:
            vers = active_versions.get(definition.name, {})
        except ValueError:
            continue
        production_version = vers.get("production", None)
        if not production_version:
            continue

        try:
            addon = definition[production_version]
        except KeyError:
            continue

        options = await addon.get_sso_options(base_url)
        if not options:
            continue

        result.extend(options)

    return result


async def get_user_sites(
    user_name: str, current_site: SiteInfo | None
) -> list[SiteInfo]:
    """Return a list of sites the user is registered to

    If site information in the request headers, it will be added to the
    top of the listand updated in the database if necessary.
    """
    sites: list[SiteInfo] = []
    current_needs_update = False
    current_site_exists = False

    query_id = current_site.id if current_site else ""

    # Get all sites the user is registered to or the current site
    query = """
        SELECT id, data FROM sites
        WHERE id = $1 OR data->'users' ? $2
    """

    async for row in Postgres.iterate(query, query_id, user_name):
        site = SiteInfo(id=row["id"], **row["data"])
        if current_site and site.id == current_site.id:
            # record matches the current site
            current_site_exists = True
            if user_name not in site.users:
                current_site.users.update(site.users)
                current_needs_update = True
            # we can use elif here, because we only need to check one condition
            elif site.platform != current_site.platform:
                current_needs_update = True
            elif site.hostname != current_site.hostname:
                current_needs_update = True
            elif site.version != current_site.version:
                current_needs_update = True
            # do not add the current site to the list,
            # we'll insert it at the beginning at the end of the loop
            continue
        sites.append(site)

    if current_site:
        # if the current site is not in the database
        # or has been changed, upsert it
        if current_needs_update or not current_site_exists:
            logger.debug(f"Registering to site {current_site.id}", user=user_name)
            mdata = current_site.dict()
            mid = mdata.pop("id")
            await Postgres.execute(
                """
                INSERT INTO sites (id, data)
                VALUES ($1, $2) ON CONFLICT (id)
                DO UPDATE SET data = EXCLUDED.data
                """,
                mid,
                mdata,
            )

        # insert the current site at the beginning of the list
        sites.insert(0, current_site)
    return sites


@aiocache.cached(ttl=5)
async def get_attributes() -> list[AttributeModel]:
    """Return a list of available attributes

    populate enum fields with values from the database
    in the case dynamic enums are used.
    """

    enums: dict[str, Any] = {}
    async for row in Postgres.iterate(
        "SELECT name, data FROM attributes WHERE data->'enum' is not null"
    ):
        enums[row["name"]] = row["data"]["enum"]

    attr_list: list[AttributeModel] = []
    for row in attribute_library.info_data:
        row = {**row}
        if row["name"] in enums:
            row["data"]["enum"] = enums[row["name"]]
        try:
            attr_list.append(AttributeModel(**row))
        except ValidationError:
            log_traceback(f"Invalid attribute data: {row}")
            continue
    return attr_list


async def get_additional_info(user: UserEntity, request: Request):
    """Return additional information for the user

    This is returned only if the user is logged in.
    """

    # Handle site information

    current_site: SiteInfo | None = None
    with contextlib.suppress(ValidationError):
        current_site = SiteInfo(
            id=request.headers.get("x-ayon-site-id"),
            platform=request.headers.get("x-ayon-platform"),
            hostname=request.headers.get("x-ayon-hostname"),
            version=request.headers.get("x-ayon-version"),
            users=[user.name],
        )

    sites = await get_user_sites(user.name, current_site)

    attr_list = await get_attributes()
    extras = await CloudUtils.get_extras()

    return {
        "attributes": attr_list,
        "sites": sites,
        "extras": extras,
    }


async def is_onboarding_finished() -> bool:
    r = await Redis.get("global", "onboardingFinished")
    if r is None:
        query = "SELECT * FROM config where key = 'onboardingFinished'"
        rdb = await Postgres.fetch(query)
        if rdb:
            await Redis.set("global", "onboardingFinished", "1")
            return True
    elif r:
        return True
    return False


#
# The actual endpoint
#


@router.get(
    "/info",
    response_model_exclude_none=True,
    tags=["System"],
    dependencies=[NoTraces],
)
async def get_site_info(
    request: Request,
    current_user: CurrentUserOptional,
    full: bool = Query(False, description="Include frontend-related information"),
) -> InfoResponseModel:
    """Return site information.

    This is the initial endpoint that is called when the user opens the page.
    It returns information about the site, the current user and the configuration.

    If the user is not logged in, only the login page message (motd) and the
    API version are returned.
    """

    additional_info = {}
    if current_user:
        additional_info = await get_additional_info(current_user, request)

        if current_user.is_admin and not current_user.is_service:
            if not await is_onboarding_finished():
                additional_info["onboarding"] = True
    elif full:
        sso_options = await get_sso_options(request)
        has_admin_user = await admin_exists()
        additional_info = {
            "sso_options": sso_options,
            "no_admin_user": (not has_admin_user) or None,
            "password_recovery_available": bool(await is_mailing_enabled()),
        }
        server_config = await get_server_config()
        customization = server_config.customization

        if customization.motd:
            additional_info["motd"] = customization.motd
        elif ayonconfig.motd:  # Deprecated
            additional_info["motd"] = ayonconfig.motd

        if customization.login_background:
            url = f"/static/customization/{customization.login_background}"
            additional_info["login_page_background"] = url
        elif ayonconfig.login_page_background:  # Deprecated
            additional_info["login_page_background"] = ayonconfig.login_page_background

        if customization.studio_logo:
            url = f"/static/customization/{customization.studio_logo}"
            additional_info["login_page_brand"] = url
        elif ayonconfig.login_page_brand:  # Deprecated
            additional_info["login_page_brand"] = ayonconfig.login_page_brand

    user_payload = current_user.payload if (current_user is not None) else None
    return InfoResponseModel(user=user_payload, **additional_info)
