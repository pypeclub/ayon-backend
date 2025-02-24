from typing import Any, Literal

from fastapi import Query

from ayon_server.addons import AddonLibrary
from ayon_server.api.dependencies import CurrentUser, Sender, SenderType
from ayon_server.api.responses import EmptyResponse
from ayon_server.entities import UserEntity
from ayon_server.events import EventStream
from ayon_server.exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
)
from ayon_server.lib.postgres import Connection, Postgres
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel, Platform

from .actions import promote_bundle
from .check_bundle import CheckBundleResponseModel, check_bundle
from .migration import migrate_settings
from .models import AddonDevelopmentItem, BundleModel, BundlePatchModel, ListBundleModel
from .router import router

#
# List all bundles
#


@router.get("/bundles", response_model_exclude_none=True)
async def list_bundles(
    user: CurrentUser,
    archived: bool = Query(False, description="Include archived bundles"),
) -> ListBundleModel:
    result: list[BundleModel] = []
    production_bundle: str | None = None
    staging_bundle: str | None = None
    dev_bundles: list[str] = []

    async for row in Postgres.iterate("SELECT * FROM bundles ORDER by created_at DESC"):
        # do not show archived bundles unless requested
        if not archived and row["is_archived"]:
            continue

        data = row["data"]

        bundle = BundleModel(
            name=row["name"],
            created_at=row["created_at"],
            addons=data.get("addons", {}),
            installer_version=data.get("installer_version"),
            dependency_packages=data.get("dependency_packages", {}),
            is_production=row["is_production"],
            is_staging=row["is_staging"],
            is_archived=row["is_archived"],
            is_dev=row["is_dev"],
            active_user=row["active_user"],
            addon_development=data.get("addon_development", {}),
        )

        # helper top-level attributes (for convenience not crawling the list)
        if row["is_production"]:
            production_bundle = row["name"]
        if row["is_staging"]:
            staging_bundle = row["name"]
        if row["is_dev"]:
            dev_bundles.append(row["name"])

        result.append(bundle)

    return ListBundleModel(
        bundles=result,
        production_bundle=production_bundle,
        staging_bundle=staging_bundle,
        dev_bundles=dev_bundles,
    )


#
# Create a new bundle
#


async def _create_new_bundle(
    conn: Connection,
    bundle: BundleModel,
    *,
    user: UserEntity | None = None,
    sender: str | None = None,
    sender_type: str | None = None,
):
    # Clear constrained values if they are being updated
    if bundle.is_production:
        await conn.execute("UPDATE bundles SET is_production = FALSE")
    if bundle.is_staging:
        await conn.execute("UPDATE bundles SET is_staging = FALSE")
    if bundle.active_user:
        await conn.execute(
            "UPDATE bundles SET active_user = NULL WHERE active_user = $1",
            bundle.active_user,
        )

    data: dict[str, Any] = {
        "addons": bundle.addons,
        "installer_version": bundle.installer_version,
        "dependency_packages": bundle.dependency_packages,
    }
    if bundle.addon_development:
        addon_development_dict = {}
        for key, value in bundle.addon_development.items():
            addon_development_dict[key] = value.dict()
        data["addon_development"] = addon_development_dict

    query = """
        INSERT INTO bundles
        (name, data, is_production, is_staging, is_dev, active_user, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    """

    # we ignore is_archived. it does not make sense to create
    # an archived bundle

    await conn.execute(
        query,
        bundle.name,
        data,
        bundle.is_production,
        bundle.is_staging,
        bundle.is_dev,
        bundle.active_user,
        bundle.created_at,
    )

    await EventStream.dispatch(
        "bundle.created",
        sender=sender,
        sender_type=sender_type,
        user=user.name if user else None,
        description=f"Bundle {bundle.name} created",
        summary={
            "name": bundle.name,
            "isProduction": bundle.is_production,
            "isStaging": bundle.is_staging,
            "isDev": bundle.is_dev,
        },
        payload=data,
    )


@router.post("/bundles/check")
async def check_bundle_compatibility(
    user: CurrentUser,
    bundle: BundleModel,
) -> CheckBundleResponseModel:
    return await check_bundle(bundle)


@router.post("/bundles", status_code=201)
async def create_new_bundle(
    bundle: BundleModel,
    user: CurrentUser,
    sender: Sender,
    sender_type: SenderType,
    force: bool = Query(False, description="Force creation of bundle"),
) -> EmptyResponse:
    if not user.is_admin:
        raise ForbiddenException("Only admins can create bundles")

    if not force:
        res = await check_bundle(bundle)
        if not res.success:
            raise BadRequestException(res.message())

    for system_addon_name, addon_definition in AddonLibrary.items():
        if addon_definition.is_system:
            if system_addon_name not in bundle.addons:
                logger.debug(
                    f"Adding system addon {system_addon_name} to bundle {bundle.name}"
                )
                if addon_definition.latest:
                    bundle.addons[system_addon_name] = addon_definition.latest.version

    if bundle.is_project:
        for addon_name in list(bundle.addons.keys()):
            addon_definition = AddonLibrary.get(addon_name)
            if addon_definition is None:
                raise BadRequestException(f"Addon {addon_name} does not exist")
            if not addon_definition.allow_project_override:
                bundle.addons.pop(addon_name)

    async with Postgres.acquire() as conn, conn.transaction():
        await _create_new_bundle(
            conn,
            bundle,
            user=user,
            sender=sender,
            sender_type=sender_type,
        )

    return EmptyResponse(status_code=201)


#
# Update a bundle
#


@router.patch("/bundles/{bundle_name}", status_code=204)
async def update_bundle(
    bundle_name: str,
    patch: BundlePatchModel,
    user: CurrentUser,
    sender: Sender,
    sender_type: SenderType,
    build: list[Platform] | None = Query(
        None,
        title="Request build",
        description="Build dependency packages for selected platforms",
    ),
    force: bool = Query(False, description="Force creation of bundle"),
) -> EmptyResponse:
    if not user.is_admin:
        raise ForbiddenException("Only admins can patch bundles")

    status_changed_to: str | None = None

    async with Postgres.acquire() as conn, conn.transaction():
        res = await conn.fetch(
            "SELECT * FROM bundles WHERE name = $1 FOR UPDATE", bundle_name
        )
        if not res:
            raise NotFoundException("Bundle not found")

        row = res[0]
        data = row["data"]

        addon_development_dict: dict[str, AddonDevelopmentItem] = {}
        for key, value in data.get("addon_development", {}).items():
            addon_development_dict[key] = AddonDevelopmentItem(**value)

        bundle = BundleModel(
            name=row["name"],
            created_at=row["created_at"],
            addons=data["addons"],
            installer_version=data.get("installer_version", None),
            dependency_packages=data.get("dependency_packages", {}),
            addon_development=addon_development_dict,
            is_production=row["is_production"],
            is_staging=row["is_staging"],
            is_dev=row["is_dev"],
            active_user=row["active_user"],
            is_archived=row["is_archived"],
        )

        if patch.is_archived and (bundle.is_production or bundle.is_staging):
            raise BadRequestException(
                "Cannot archive bundle that is production or staging"
            )

        #
        # Dev specific fields
        #

        if bundle.is_dev:
            logger.debug(f"Updating dev bundle {bundle.name}")
            if patch.active_user is not None:
                # remove user from previously assigned bundles
                # to avoid constraint violation
                await conn.execute(
                    "UPDATE bundles SET active_user = NULL WHERE active_user = $1",
                    patch.active_user,
                )
                bundle.active_user = patch.active_user

            if patch.addon_development is not None:
                bundle.addon_development = patch.addon_development

            if patch.installer_version is not None:
                bundle.installer_version = patch.installer_version
        else:
            logger.debug(f"Updating bundle {bundle.name}")
            bundle.active_user = None

        # Dependency packages
        # Can be patched for both dev and non-dev bundles

        if patch.dependency_packages is not None:
            bundle.dependency_packages = patch.dependency_packages

        # Addons
        # Can be patched for both dev and non-dev bundles
        # But when patching a non-dev bundle, only server addons can be patched

        if patch.addons is not None:
            library = AddonLibrary.getinstance()
            addons = {**bundle.addons}
            for addon_name, addon_version in patch.addons.items():
                addon_definition = library.get(addon_name)
                if addon_definition is None:
                    logger.warning(f"Addon {addon_name} does not exist, ignoring")
                    continue
                is_server = addon_definition.addon_type == "server"
                if not bundle.is_dev and not is_server:
                    pass

                if addon_version is None:
                    addons.pop(addon_name, None)
                    continue

                # TODO: check if addon version exists
                addons[addon_name] = addon_version
            bundle.addons = addons

        # Validate the bundle

        if not force:
            bstat = await check_bundle(bundle)
            if not bstat.success:
                raise BadRequestException(bstat.message())

        # Construct the new data

        data = {
            "addons": bundle.addons,
            "dependency_packages": bundle.dependency_packages,
            "installer_version": bundle.installer_version,
            "is_project": bundle.is_project,
        }
        if bundle.is_dev:
            data["addon_development"] = {
                key: value.dict() for key, value in bundle.addon_development.items()
            }

        if patch.is_archived is not None:
            bundle.is_archived = patch.is_archived

        if patch.is_dev is not None:
            bundle.is_dev = patch.is_dev

        if patch.is_production is not None:
            if patch.is_production:
                await conn.execute("UPDATE bundles SET is_production = FALSE")
            bundle.is_production = patch.is_production

        if patch.is_staging is not None:
            if patch.is_staging:
                await conn.execute("UPDATE bundles SET is_staging = FALSE")
            bundle.is_staging = patch.is_staging

        # Update the bundle

        await conn.execute(
            """
            UPDATE bundles
            SET
                data = $1,
                is_production = $2,
                is_staging = $3,
                is_dev = $4,
                active_user = $5,
                is_archived = $6
            WHERE name = $7
            """,
            data,
            bundle.is_production,
            bundle.is_staging,
            bundle.is_dev,
            bundle.active_user,
            bundle.is_archived,
            bundle_name,
        )

    await EventStream.dispatch(
        "bundle.updated",
        sender=sender,
        sender_type=sender_type,
        user=user.name,
        description=f"Bundle {bundle_name} updated",
        summary={
            "name": bundle_name,
            "isProduction": bundle.is_production,
            "isStaging": bundle.is_staging,
            "isArchived": bundle.is_archived,
            "isDev": bundle.is_dev,
            "isProject": bundle.is_project,
        },
        payload=data,
    )

    if status_changed_to:
        await EventStream.dispatch(
            "bundle.status_changed",
            sender=sender,
            sender_type=sender_type,
            user=user.name,
            description=f"Bundle {bundle_name} changed to {status_changed_to}",
            summary={
                "name": bundle_name,
                "status": status_changed_to,
            },
            payload=data,
        )

    if build:
        # TODO
        pass

    return EmptyResponse(status_code=204)


#
# Delete bundle
#


async def delete_bundle(bundle_name: str):
    await Postgres.execute("DELETE FROM bundles WHERE name = $1", bundle_name)


@router.delete("/bundles/{bundle_name}", status_code=204)
async def delete_existing_bundle(
    bundle_name: str,
    user: CurrentUser,
) -> EmptyResponse:
    if not user.is_admin:
        raise ForbiddenException("Only admins can delete bundles")
    await delete_bundle(bundle_name)
    return EmptyResponse(status_code=204)


#
# Bundle actions
#


class BundleActionModel(OPModel):
    action: Literal["promote"] = Field(..., example="promote")


@router.post("/bundles/{bundle_name}", status_code=201)
async def bundle_actions(
    bundle_name: str,
    action: BundleActionModel,
    user: CurrentUser,
) -> EmptyResponse:
    """Perform actions on bundles."""

    async with Postgres.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetch(
                "SELECT * FROM bundles WHERE name = $1 FOR UPDATE", bundle_name
            )
            if not res:
                raise NotFoundException("Bundle not found")
            row = res[0]
            bundle = BundleModel(
                **row["data"],
                name=row["name"],
                created_at=row["created_at"],
                is_production=row["is_production"],
                is_staging=row["is_staging"],
                is_archived=row["is_archived"],
                is_dev=row["is_dev"],
            )

            if bundle.is_archived:
                raise BadRequestException("Archived bundles cannot be modified")

            if action.action == "promote":
                await promote_bundle(bundle, user, conn)

    return EmptyResponse(status_code=204)


class MigrateBundleSettingsRequest(OPModel):
    source_bundle: str = Field(..., example="old-bundle", description="Source bundle")
    target_bundle: str = Field(..., example="new-bundle", description="Target bundle")
    source_variant: str = Field(..., example="production", description="Source variant")
    target_variant: str = Field(..., example="staging", description="Target variant")
    with_projects: bool = Field(
        True,
        example=True,
        description="Migrate project settings",
    )


@router.post("/migrateSettingsByBundle")
async def migrate_settings_by_bundle(
    user: CurrentUser,
    request: MigrateBundleSettingsRequest,
) -> None:
    """Migrate settings of the addons based on the bundles.

    When called, it collects a list of addons that are present in
    both source and target bundles and migrates the settings of the
    addons from the source to the target bundle.

    Target bundle should be a production or staging bundle (or a dev bundle),
    but source bundle can be any bundle.
    """
    if not user.is_admin:
        raise ForbiddenException("Only admins can migrate bundle settings")

    await migrate_settings(
        request.source_bundle,
        request.target_bundle,
        request.source_variant,
        request.target_variant,
        request.with_projects,
        user_name=user.name,
    )
