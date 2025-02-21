import asyncio
import os

import aiofiles
from fastapi import Response

from ayon_server.api.files import image_response_from_bytes
from ayon_server.exceptions import (
    NotFoundException,
    ServiceUnavailableException,
    UnsupportedMediaException,
)
from ayon_server.files import Storages
from ayon_server.helpers.mimetypes import is_image_mime_type, is_video_mime_type
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from nxtools import logging

REDIS_NS = "project.file_preview"
FILE_PREVIEW_SIZE = (600, None)
PREVIEW_CACHE_TTL = 3600 * 24


VIDEO_THUMBNAIL_STATUS = set()


async def create_video_thumbnail(
    video_path: str,
    size: tuple[int | None, int | None] | None = None,
    timestamp: float | None = None,
) -> bytes:
    """Create a thumbnail image for a video file.

    Returns the thumbnail image as bytes.
    """
    global VIDEO_THUMBNAIL_STATUS

    if video_path in VIDEO_THUMBNAIL_STATUS:
        print("Video thumbnail generation is in progress")
        raise ServiceUnavailableException("Video thumbnail generation is in progress")

    VIDEO_THUMBNAIL_STATUS.add(video_path)
    try:
        async with aiofiles.tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=True
        ) as temp_file:
            temp_path = str(temp_file.name)

            cmd: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

            if timestamp is not None:
                cmd.extend(["-ss", str(timestamp)])

            cmd.extend(["-y", "-i", video_path])

            if size is not None:
                cmd.extend(
                    [
                        "-filter:v",
                        f"scale={size[0] or -1 }:{size[1] or -1}",
                    ]
                )

            cmd.extend(
                [
                    "-frames:v",
                    "1",
                    "-c:v",
                    "mjpeg",
                    temp_path,
                ]
            )

            logging.debug(" ".join(cmd))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stderr=asyncio.subprocess.PIPE,
            )

            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                stderr_str = stderr.decode()
                print(stderr_str, flush=True)
                return b""

            async with aiofiles.open(temp_path, "rb") as f:
                image_bytes = await f.read()

        return image_bytes
    finally:
        VIDEO_THUMBNAIL_STATUS.remove(video_path)


async def obtain_file_preview(project_name: str, file_id: str) -> bytes:
    """Return a preview image for a file as bytes.

    Raises:
        - UnsupportedMediaException if the mimetype is not supported
        for preview generation.
        - NotFoundException if the file or file record is not found.
    """

    res = await Postgres.fetch(
        f"""
        SELECT size, data FROM project_{project_name}.files
        WHERE id = $1
        """,
        file_id,
    )

    if not res:
        raise NotFoundException("File record not found")
    file_record = res[0]
    file_data = file_record["data"] or {}
    expected_size = res[0]["size"]
    mime_type = file_data.get("mime", "application/octet-stream")

    # Get the file location

    storage = await Storages.project(project_name)

    if storage.storage_type == "local":
        path = await storage.get_path(file_id)

        if not os.path.isfile(path):
            raise NotFoundException("File not found")

        if os.path.getsize(path) != expected_size:
            logging.warning(f"File size mismatch: {path}")

    elif storage.storage_type == "s3":
        path = await storage.get_signed_url(file_id)

    if is_video_mime_type(mime_type):
        pvw_bytes = await create_video_thumbnail(path, FILE_PREVIEW_SIZE)
        return pvw_bytes

    if is_image_mime_type(mime_type):
        # async with aiofiles.open(path, "rb") as f:
        #     image_bytes = await f.read()
        #     pvw_bytes = await process_thumbnail(
        #         image_bytes,
        #         FILE_PREVIEW_SIZE,
        #         format="JPEG",
        #     )
        #     return pvw_bytes

        # TODO: unify. for now use ffmpeg for images as well (since it handles s3
        pvw_bytes = await create_video_thumbnail(path, FILE_PREVIEW_SIZE)
        return pvw_bytes

    # TODO: return a generic preview image for other file types
    raise UnsupportedMediaException("Preview mode is not supported for this file")


async def get_file_preview(
    project_name: str, file_id: str, retries: int = 0
) -> Response:
    """Return a preview image for a file.

    Uses the cache if available, otherwise generates a new preview and caches it.
    Returns fastapi.Response object with the image data.
    """
    file_id = file_id.replace("-", "")
    assert len(file_id) == 32

    key = f"{project_name}.{file_id}"
    pvw_bytes = await Redis.get(REDIS_NS, key)

    if pvw_bytes is None:
        try:
            pvw_bytes = await obtain_file_preview(project_name, file_id)
            await Redis.set(REDIS_NS, key, pvw_bytes, ttl=PREVIEW_CACHE_TTL)
        except ServiceUnavailableException:
            await asyncio.sleep(0.2)
            if retries < 5:
                return await get_file_preview(project_name, file_id, retries + 1)

    if pvw_bytes == b"":
        raise NotFoundException("File preview not available")

    return image_response_from_bytes(pvw_bytes)


async def uncache_file_preview(project_name: str, file_id: str) -> None:
    """Remove the preview image from the cache.

    Silently ignore if the file is not found in the cache.
    """
    file_id = file_id.replace("-", "")
    if len(file_id) != 32:
        raise ValueError("Invalid file ID")
    key = f"{project_name}.{file_id}"
    await Redis.delete(REDIS_NS, key)
