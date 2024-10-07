import os
from typing import Any, Literal

import aiocache
import aiofiles
import aioshutil
from fastapi import Request
from nxtools import logging

from ayon_server.api.files import handle_upload
from ayon_server.config import ayonconfig
from ayon_server.exceptions import AyonException
from ayon_server.files.s3 import (
    S3Config,
    delete_s3_file,
    get_signed_url,
    handle_s3_upload,
    retrieve_s3_file,
    store_s3_file,
    upload_s3_file,
)
from ayon_server.helpers.cloud import get_instance_id
from ayon_server.helpers.ffprobe import extract_media_info
from ayon_server.lib.postgres import Postgres

StorageType = Literal["local", "s3"]
FileGroup = Literal["uploads", "thumbnails"]


class ProjectStorage:
    storage_type: StorageType = "local"
    root: str
    bucket_name: str | None = None
    cdn_resolver: str | None = None
    _s3_client: Any = None

    def __init__(
        self,
        project_name: str,
        storage_type: StorageType,
        root: str,
        bucket_name: str | None = None,
        cdn_resolver: str | None = None,
        s3_config: S3Config | None = None,
    ):
        self.project_name = project_name
        self.storage_type = storage_type
        self.root = root
        if storage_type == "s3":
            if not bucket_name:
                raise Exception("Bucket name is required")

            self.bucket_name = bucket_name
            self.s3_config = s3_config or S3Config()
        self.cdn_resolver = cdn_resolver

    def __repr__(self) -> str:
        return f"<ProjectStorage {self.project_name} {self.storage_type}>"

    def __str__(self) -> str:
        return f"{self.project_name} {self.storage_type} storage"

    # Base storage methods

    @classmethod
    def default(cls, project_name: str) -> "ProjectStorage":
        if ayonconfig.default_project_storage_type == "local":
            return cls(
                project_name,
                "local",
                ayonconfig.default_project_storage_root,
                cdn_resolver=ayonconfig.default_project_storage_cdn_resolver,
            )
        elif ayonconfig.default_project_storage_type == "s3":
            return cls(
                project_name,
                "s3",
                ayonconfig.default_project_storage_root.lstrip("/"),
                bucket_name=ayonconfig.default_project_storage_bucket_name,
                cdn_resolver=ayonconfig.default_project_storage_cdn_resolver,
            )

        raise Exception("Unknown storage type. This should not happen.")

    @aiocache.cached()
    async def get_root(self) -> str:
        instance_id = await get_instance_id()
        return self.root.format(instance_id=instance_id)

    # Common file management methods

    async def get_path(
        self,
        file_id: str,
        file_group: FileGroup = "uploads",
    ) -> str:
        """Return the full path to the file on the storage

        In the case of S3, the resulting path is used as the key (relative
        path from the bucket), while in the case of local storage, it's
        the full path to the file on the disk.
        """
        root = await self.get_root()
        assert file_group in ["uploads", "thumbnails"], "Invalid file group"
        _file_id = file_id.replace("-", "")
        if len(_file_id) != 32:
            raise ValueError(f"Invalid file ID: {file_id}")
        # Take first two characters of the file ID as a subdirectory
        # to avoid having too many files in a single directory
        sub_dir = _file_id[:2]
        return os.path.join(
            root,
            self.project_name,
            file_group,
            sub_dir,
            _file_id,
        )

    async def upload_file(self, file_id: str, file_path: str) -> None:
        """Store the locally accessible project file on the storage"""

        target_path = await self.get_path(file_id)
        if self.storage_type == "local":
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)
            await aioshutil.copyfile(file_path, target_path)
        elif self.storage_type == "s3":
            await upload_s3_file(self, target_path, file_path)

    async def get_signed_url(
        self,
        file_id: str,
        file_group: FileGroup = "uploads",
        ttl: int = 3600,
    ) -> str:
        """Return a signed URL to access the file on the storage over HTTP

        This method is only supported for S3 storages.
        """
        if self.storage_type == "s3":
            path = await self.get_path(file_id, file_group=file_group)
            assert self.bucket_name  # mypy
            return await get_signed_url(self, path, ttl)
        raise Exception("Signed URLs are only supported for S3 storage")

    async def handle_upload(
        self,
        request: Request,
        file_id: str,
        file_group: FileGroup = "uploads",
    ) -> int:
        """Handle file upload request

        Takes an incoming FastAPI request and saves the file to the
        project storage. Returns the number of bytes written.
        """
        logging.debug(f"Uploading file {file_id} to {self} ({file_group})")
        path = await self.get_path(file_id, file_group=file_group)
        if self.storage_type == "local":
            return await handle_upload(request, path)
        elif self.storage_type == "s3":
            return await handle_s3_upload(self, request, path)
        raise Exception("Unknown storage type")

    async def unlink(
        self,
        file_id: str,
        file_group: FileGroup = "uploads",
    ) -> bool:
        """Delete file from the storage if exists

        Database is not affected. Should be used for temporary files,
        (files that weren't stored to the DB yet), or for cleaning up
        files with missing DB records.
        """

        path = await self.get_path(file_id, file_group=file_group)
        if self.storage_type == "local":
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logging.error(f"Failed to delete file: {e}")
                return False

            directory = os.path.dirname(path)
            if os.path.isdir(directory) and (not os.listdir(directory)):
                try:
                    os.rmdir(directory)
                except Exception as e:
                    logging.error(f"Failed to delete directory on {self}: {e}")
            return True

        elif self.storage_type == "s3":
            assert self.bucket_name  # mypy
            try:
                await delete_s3_file(self, path)
            except Exception as e:
                logging.error(f"Failed to delete file: {e}")
                return False
            return True
        else:
            raise Exception("Unknown storage type")

    # Project files (uploads) methods
    # for comment attachment and reviewables

    async def delete_file(self, file_id: str) -> None:
        """Delete file from the storage and database"""

        if not await self.unlink(file_id):
            raise Exception("Failed to delete file")

        query = f"""
            DELETE FROM project_{self.project_name}.files
            WHERE id = $1
        """
        await Postgres.execute(query, file_id)
        query = f"""
            WITH updated_activities AS (
                SELECT
                    id,
                    jsonb_set(
                        data,
                        '{{files}}',
                        (
                            SELECT jsonb_agg(elem)
                            FROM jsonb_array_elements(data->'files') elem
                            WHERE elem->>'id' != '{file_id}'
                        )
                    ) AS new_data
                FROM
                    project_{self.project_name}.activities
                WHERE
                    data->'files' @> jsonb_build_array(
                        jsonb_build_object('id', '{file_id}')
                    )
            )
            UPDATE project_{self.project_name}.activities
            SET data = updated_activities.new_data
            FROM updated_activities
            WHERE activities.id = updated_activities.id;
        """

        await Postgres.execute(query)

        # prevent circular import
        from ayon_server.helpers.preview import uncache_file_preview

        await uncache_file_preview(self.project_name, file_id)

    async def delete_unused_files(self) -> None:
        """Delete project files that are not referenced in any activity."""

        query = f"""
            SELECT id FROM project_{self.project_name}.files
            WHERE activity_id IS NULL
            AND updated_at < NOW() - INTERVAL '5 minutes'
        """

        async for row in Postgres.iterate(query):
            logging.debug(f"Deleting unused file {row['id']} from {self}")
            try:
                await self.delete_file(row["id"])
            except Exception:
                pass

    async def extract_media_info(self, file_id: str) -> dict[str, Any]:
        """Extract media info from the file

        Returns a dictionary with media information.
        """
        if self.storage_type == "local":
            path = await self.get_path(file_id)
        elif self.storage_type == "s3":
            path = await self.get_signed_url(file_id)
        else:
            raise AyonException("Unknown storage type")
        return await extract_media_info(path)

    # Thumbnail methods
    # Used for storing original images of the thumbnail
    # in order to keep database size small

    async def store_thumbnail(self, thumbnail_id: str, payload: bytes) -> None:
        """Store the thumbnail image in the storage."""
        logging.debug(f"Storing thumbnail {thumbnail_id} to {self}")
        path = await self.get_path(thumbnail_id, file_group="thumbnails")
        if self.storage_type == "local":
            directory, _ = os.path.split(path)
            if not os.path.isdir(directory):
                try:
                    os.makedirs(directory)
                except Exception as e:
                    raise AyonException(f"Failed to create directory: {e}") from e

            try:
                async with aiofiles.open(path, "wb") as f:
                    await f.write(payload)
            except Exception as e:
                raise AyonException(f"Failed to write file: {e}") from e
        elif self.storage_type == "s3":
            return await store_s3_file(self, path, payload)

    async def get_thumbnail(self, thumbnail_id: str) -> bytes:
        """Retrieve the thumbnail image from the storage.

        Raises `FileNotFoundError` if the thumbnail is not found.
        """
        path = await self.get_path(thumbnail_id, file_group="thumbnails")
        if self.storage_type == "local":
            try:
                async with aiofiles.open(path, "rb") as f:
                    return await f.read()
            except FileNotFoundError as e:
                raise FileNotFoundError(
                    f"Thumbnail {thumbnail_id} not found on {self}"
                ) from e
            except Exception as e:
                raise AyonException(f"Failed to read file: {e}") from e
        return await retrieve_s3_file(self, path)

    async def delete_thumbnail(self, thumbnail_id: str) -> None:
        """Delete the thumbnail image from the storage.

        Fail silently if the thumbnail is not found.
        """
        logging.debug(f"Deleting thumbnail {thumbnail_id} from {self}")
        await self.unlink(thumbnail_id, file_group="thumbnails")
