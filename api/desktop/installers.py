import hashlib
import os
from typing import Literal

import aiofiles
from fastapi import BackgroundTasks, Query, Request

from ayon_server.api.dependencies import CurrentUser
from ayon_server.api.files import handle_download, handle_upload
from ayon_server.api.responses import EmptyResponse
from ayon_server.events import EventStream
from ayon_server.exceptions import (
    AyonException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from ayon_server.installer import background_installer
from ayon_server.installer.models import (
    InstallerManifest,
    SourceModel,
    SourcesPatchModel,
)
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel, Platform

from .common import (
    InstallResponseModel,
    get_desktop_dir,
    get_desktop_file_path,
    iter_names,
    load_json_file,
)
from .router import router

#
# Models
#


class Installer(InstallerManifest):
    @property
    def local_file_path(self) -> str:
        return get_desktop_file_path("installers", self.filename)

    @property
    def has_local_file(self) -> bool:
        return os.path.isfile(self.local_file_path)

    @property
    def path(self) -> str:
        return get_desktop_file_path("installers", f"{self.filename}.json")


class InstallerListModel(OPModel):
    installers: list[Installer] = Field(default_factory=list)


#
# Helpers
#


def get_manifest(filename: str) -> Installer:
    try:
        manifest_data = load_json_file("installers", f"{filename}.json")
        manifest = Installer(**manifest_data)
    except FileNotFoundError:
        raise NotFoundException(f"Installer manifest {filename} not found")
    except ValueError:
        raise AyonException(f"Failed to load installer manifest {filename}")
    if manifest.has_local_file:
        if "server" not in [s.type for s in manifest.sources]:
            manifest.sources.insert(0, SourceModel(type="server"))

    manifest.sources = [
        m
        for m in manifest.sources
        if not (m.url and m.url.startswith("https://download.ynput.cloud"))
    ]

    return manifest


#
# API
#


@router.get("/installers", response_model_exclude_none=True)
async def list_installers(
    user: CurrentUser,
    version: str | None = Query(None, description="Version of the package"),
    platform: Platform | None = Query(None, description="Platform of the package"),
    variant: Literal["production", "staging"] | None = Query(None),
) -> InstallerListModel:
    result: list[Installer] = []

    if variant in ["production", "staging"]:
        r = await Postgres.fetch(
            f"""
            SELECT data->>'installer_version' as v
            FROM bundles WHERE is_{variant} IS TRUE
            """
        )
        if r:
            version = r[0]["v"]
        else:
            raise NotFoundException(f"No {variant} bundle found")

    for filename in iter_names("installers"):
        try:
            manifest = get_manifest(filename)
        except Exception as e:
            logger.warning(f"Failed to load manifest file {filename}: {e}")
            continue

        if filename != manifest.filename:
            logger.warning(
                f"Filenames in manifest don't match: {filename} != {manifest.filename}"
            )
            continue

        # Filtering

        if platform is not None and platform != manifest.platform:
            continue

        if version is not None and version != manifest.version:
            continue

        result.append(manifest)
    return InstallerListModel(installers=result)


@router.post("/installers", status_code=201)
async def create_installer(
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    payload: Installer,
    url: str | None = Query(None, description="URL to the addon zip file"),
    overwrite: bool = Query(
        False, description="Deprecated. Use the force", deprecated=True
    ),
    force: bool = Query(False, description="Overwrite existing installer"),
) -> InstallResponseModel:
    event_id: str | None = None

    if not user.is_admin:
        raise ForbiddenException("Only admins can create installers")

    force = force or overwrite

    try:
        _ = get_manifest(payload.filename)
    except Exception:
        pass
    else:
        if not force:
            raise ConflictException("Installer already exists")

    _ = get_desktop_dir("installers", for_writing=True)

    if not force:
        # double-check - filename check might not be enough,
        # we must check whether there is a manifest with the same version and Platform
        existing_installers = await list_installers(
            user=user,
            version=payload.version,
            platform=payload.platform,
            variant=None,
        )
        if existing_installers.installers:
            raise AyonException(
                "Installer with the same version and platform already exists"
            )

    if url:
        hash = hashlib.sha256(f"installer_install_{url}".encode()).hexdigest()

        query = """
            SELECT id FROM events
            WHERE topic = 'installer.install_from_url'
            AND hash = $1
        """

        res = await Postgres.fetch(query, hash)
        if res:
            event_id = res[0]["id"]

            assert event_id

            await EventStream.update(
                event_id,
                description="Reinstalling installer from URL",
                summary={"url": url},
                status="pending",
                retries=0,
            )
        else:
            event_id = await EventStream.dispatch(
                "installer.install_from_url",
                hash=hash,
                description="Installing installer from URL",
                summary={"url": url},
                user=user.name,
                finished=False,
            )

        assert event_id
        background_tasks.add_task(background_installer.enqueue, event_id)

    async with aiofiles.open(payload.path, "w") as f:
        await f.write(payload.json(exclude_none=True))

    return InstallResponseModel(event_id=event_id)


@router.get("/installers/{filename}")
async def download_installer_file(user: CurrentUser, filename: str):
    installers_dir = get_desktop_dir("installers", for_writing=False)
    file_path = os.path.join(installers_dir, filename)
    return await handle_download(file_path)


@router.put("/installers/{filename}", status_code=204)
async def upload_installer_file(user: CurrentUser, request: Request, filename: str):
    if not user.is_admin:
        raise ForbiddenException("Only admins can upload installers")

    manifest = get_manifest(filename)

    if manifest.filename != filename:
        raise AyonException("Filename in manifest does not match")

    return await handle_upload(request, manifest.local_file_path)


@router.delete("/installers/{filename}", status_code=204)
async def delete_installer_file(user: CurrentUser, filename: str):
    if not user.is_admin:
        raise ForbiddenException("Only admins can delete installers")
    manifest = get_manifest(filename)
    if manifest.has_local_file:
        os.remove(manifest.local_file_path)
    os.remove(manifest.path)
    return EmptyResponse(status_code=204)


@router.patch("/installers/{filename}", status_code=204)
async def patch_installer(user: CurrentUser, filename: str, payload: SourcesPatchModel):
    """Update sources for an installer"""
    if not user.is_admin:
        raise ForbiddenException("Only admins can patch installers")

    manifest = get_manifest(filename)

    if manifest.filename != filename:
        raise AyonException("Filename in manifest does not match")

    manifest.sources = payload.sources

    async with aiofiles.open(manifest.path, "w") as f:
        await f.write(manifest.json(exclude_none=True))
    return EmptyResponse(status_code=204)
