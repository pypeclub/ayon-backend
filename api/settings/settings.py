import traceback
from typing import Any

from fastapi import Query

from ayon_server.addons import AddonLibrary
from ayon_server.api.dependencies import CurrentUser, SiteID
from ayon_server.exceptions import NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import log_traceback, logger
from ayon_server.settings import BaseSettingsModel
from ayon_server.types import NAME_REGEX, SEMVER_REGEX, Field, OPModel

from .router import router


class AddonSettingsItemModel(OPModel):
    name: str = Field(..., title="Addon name", regex=NAME_REGEX, example="my-addon")
    version: str = Field(
        ..., title="Addon version", regex=SEMVER_REGEX, example="1.0.0"
    )
    title: str = Field(..., title="Addon title", example="My Addon")

    has_settings: bool = Field(False)
    has_project_settings: bool = Field(False)
    has_project_site_settings: bool = Field(False)
    has_site_settings: bool = Field(False)

    # None value means that project does not have overrides
    # or project/site was not specified in the request
    has_studio_overrides: bool | None = Field(None)
    has_project_overrides: bool | None = Field(None)
    has_project_site_overrides: bool | None = Field(None)

    # Final settings for the addon depending on the request (project, site)
    # it returns either studio, project or project/site settings
    settings: dict[str, Any] = Field(default_factory=dict)

    # If site_id is specified and the addon has site settings model,
    # return studio level site settings here
    site_settings: dict[str, Any] | None = Field(default_factory=dict)

    is_broken: bool = Field(False)
    reason: dict[str, str] | None = Field(None)


class AllSettingsResponseModel(OPModel):
    bundle_name: str = Field(..., regex=NAME_REGEX)
    addons: list[AddonSettingsItemModel] = Field(default_factory=list)
    inherited_addons: list[str] = Field(
        default_factory=list,
        description="In the case of project bundle, list of addons "
        "that are inherited from the studio bundle",
    )


@router.get("/settings", response_model_exclude_none=True)
async def get_all_settings(
    user: CurrentUser,
    site_id: SiteID,
    bundle_name: str | None = Query(
        None,
        title="Bundle name",
        description="Production if not set",
        regex=NAME_REGEX,
    ),
    project_name: str | None = Query(
        None,
        title="Project name",
        description="Studio settings if not set",
        regex=NAME_REGEX,
    ),
    variant: str = Query("production"),
    summary: bool = Query(False, title="Summary", description="Summary mode"),
) -> AllSettingsResponseModel:
    has_project_bundle_override = False
    if project_name and (not bundle_name) and variant in ("production", "staging"):
        # get project bundle overrides

        r = await Postgres.fetch(
            "SELECT data->'bundle' as bundle FROM projects WHERE name = $1",
            project_name,
        )
        if not r:
            raise NotFoundException(status_code=404, detail="Project not found")
        try:
            bundle_name = r[0]["bundle"][variant]
            has_project_bundle_override = True
        except Exception:
            pass  # no bundle override, we don't care

    if variant not in ("production", "staging"):
        query = [
            """
            SELECT name, is_production, is_staging, data->'addons' as addons
            FROM bundles WHERE name = $1
            """,
            variant,
        ]
    elif bundle_name is None:
        query = [
            f"""
            SELECT name, is_production, is_staging, data->'addons' as addons
            FROM bundles WHERE is_{variant} IS TRUE
            """
        ]
    else:
        query = [
            """
            SELECT name, is_production, is_staging, data->'addons' as addons
            FROM bundles WHERE name = $1
            """,
            bundle_name,
        ]

    brow = await Postgres.fetch(*query)
    if not brow:
        raise NotFoundException(status_code=404, detail="Bundle not found")

    bundle_name = brow[0]["name"]
    addons: dict[str, str] = brow[0]["addons"]  # {addon_name: addon_version}

    inherited_addons: list[str] = []
    if has_project_bundle_override:
        # if project has bundle override, merge it with the studio bundle
        logger.debug("got project bundle. loading studio")
        r = await Postgres.fetch(
            f"""
            SELECT name, is_production, is_staging, data->'addons' as addons
            FROM bundles WHERE is_{variant} IS TRUE
            """
        )
        if not r:
            raise NotFoundException(
                status_code=404,
                detail="Unable to load project bundle. "
                f"Studio {variant} bundle is not set",
            )
        studio_addons = r[0]["addons"]
        logger.debug(f"Studio addons: {studio_addons}")
        for addon_name, addon_version in studio_addons.items():
            studio_addon_definition = AddonLibrary.get(addon_name)
            if studio_addon_definition is None:
                logger.debug(f"cannot find addon {addon_name}")
                continue
            if studio_addon_definition.project_can_override_addon_version:
                logger.debug(
                    f"addon {addon_name} is allowed to override project bundle"
                )
                continue
            addons[addon_name] = addon_version
            inherited_addons.append(addon_name)

    addon_result = []
    for addon_name, addon_version in addons.items():
        if addon_version is None:
            continue

        try:
            addon = AddonLibrary.addon(addon_name, addon_version)
        except NotFoundException:
            logger.warning(
                f"Addon {addon_name} {addon_version} "
                f"declared in {bundle_name} not found"
            )

            broken_reason = AddonLibrary.is_broken(addon_name, addon_version)

            addon_result.append(
                AddonSettingsItemModel(
                    name=addon_name,
                    title=addon_name,
                    version=addon_version,
                    settings={},
                    site_settings=None,
                    is_broken=bool(broken_reason),
                    reason=broken_reason,
                )
            )
            continue

        # Determine which scopes addon has settings for

        model = addon.get_settings_model()
        has_settings = False
        has_project_settings = False
        has_project_site_settings = False
        has_site_settings = bool(addon.site_settings_model)
        if model:
            has_project_settings = False
            for field_name, field in model.__fields__.items():
                scope = field.field_info.extra.get("scope", ["studio", "project"])
                if "project" in scope:
                    has_project_settings = True
                if "site" in scope:
                    has_project_site_settings = True
                if "studio" in scope:
                    has_settings = True

        # Load settings for the addon

        site_settings = None
        settings: BaseSettingsModel | None = None

        try:
            if site_id:
                site_settings = await addon.get_site_settings(user.name, site_id)

                if project_name is None:
                    # Studio level settings (studio level does not have)
                    # site overrides per se but it can have site settings
                    settings = await addon.get_studio_settings(variant)
                else:
                    # Project and site is requested, so we are returning
                    # project level settings WITH site overrides
                    settings = await addon.get_project_site_settings(
                        project_name,
                        user.name,
                        site_id,
                        variant,
                    )
            elif project_name:
                # Project level settings (no site overrides)
                settings = await addon.get_project_settings(project_name, variant)
            else:
                # Just studio level settings (no project, no site)
                settings = await addon.get_studio_settings(variant)

        except Exception:
            log_traceback(f"Unable to load {addon_name} {addon_version} settings")
            addon_result.append(
                AddonSettingsItemModel(
                    name=addon_name,
                    title=addon_name,
                    version=addon_version,
                    settings={},
                    site_settings=None,
                    is_broken=True,
                    reason={
                        "error": "Unable to load settings",
                        "traceback": traceback.format_exc(),
                    },
                )
            )
            continue

        # Add addon to the result

        addon_result.append(
            AddonSettingsItemModel(
                name=addon_name,
                title=addon.title if addon.title else addon_name,
                version=addon_version,
                # Has settings means that addon has settings model
                has_settings=has_settings,
                has_project_settings=has_project_settings,
                has_project_site_settings=has_project_site_settings,
                has_site_settings=has_site_settings,
                # Has overrides means that addon has overrides for the requested
                # project/site
                has_studio_overrides=settings._has_studio_overrides
                if settings
                else None,
                has_project_overrides=settings._has_project_overrides
                if settings
                else None,
                has_project_site_overrides=settings._has_site_overrides
                if settings
                else None,
                settings=settings.dict() if (settings and not summary) else {},
                site_settings=site_settings,
            )
        )

    addon_result.sort(key=lambda x: x.title.lower())

    assert (
        bundle_name is not None
    ), "Bundle name is None"  # won't happen, shut up pyright

    logger.debug(f"Inherited addons: {inherited_addons}")
    return AllSettingsResponseModel(
        bundle_name=bundle_name,
        addons=addon_result,
        inherited_addons=inherited_addons,
    )
