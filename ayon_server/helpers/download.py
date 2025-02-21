import os
import time
import uuid
from collections.abc import Awaitable, Callable

import aiofiles
import httpx

from ayon_server.config import ayonconfig
from ayon_server.logging import logger
from ayon_server.models.file_info import FileInfo


def shorten_string(s: str, length: int) -> str:
    """If the given string is longer than the specified length,
    it will be shortened by removing the middle part and replacing it with '...'.
    """

    if len(s) <= length:
        return s

    half = length // 2
    return s[: half - 1] + "..." + s[-half:]


def get_file_name_from_headers(headers: dict[str, str]) -> str | None:
    """Parse the Content-Disposition header to extract the filename."""
    headers = {k.lower(): v for k, v in headers.items()}
    header = headers.get("content-disposition")
    if header is None:
        return None
    parts = header.split(";")
    for part in parts:
        if part.strip().startswith("filename="):
            filename = part.split("=")[1].strip()
            if filename.startswith('"') and filename.endswith('"'):
                filename = filename[1:-1]
            return filename
    return None


async def download_file(
    url: str,
    target_path: str,
    *,
    filename: str | None = None,
    progress_handler: Callable[[int], Awaitable[None]] | None = None,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    method: str = "GET",
) -> FileInfo:
    """Downloads a file from a url to a target path

    Returns the size of the file downloaded
    """

    if os.path.isdir(target_path):
        directory = target_path
        _filename = None
    else:
        directory, _filename = os.path.split(target_path)

    file_size = 0
    last_percent = 0
    last_time = 0.0

    async def handle_progress(i: int) -> None:
        nonlocal file_size
        nonlocal last_percent
        nonlocal last_time

        if progress_handler is None:
            return

        if not file_size:
            # Avoid division by zero
            return

        percent = i / file_size * 100
        if percent - last_percent < 1:
            return

        if time.time() - last_time < 1:
            return

        last_time = time.time()
        try:
            await progress_handler(int(percent))
        except Exception as e:
            # Log the error and continue
            # It should not affect the download process
            logger.debug(f"Error in download progress handler: {e}")

    i = 0

    try:
        async with httpx.AsyncClient(
            timeout=timeout or ayonconfig.http_timeout,
            follow_redirects=True,
        ) as client:
            async with client.stream(
                method,
                url,
                headers=headers,
                params=params,
            ) as response:
                if response.status_code != 200:
                    raise Exception(
                        f"Failed to download file: Error {response.status_code}"
                    )

                if filename is None:
                    # Filename is not explicitly set,
                    if _filename:
                        # But the path is a file path, use the filename from the path
                        filename = _filename

                    elif _cd_fname := get_file_name_from_headers(
                        dict(response.headers)
                    ):
                        filename = _cd_fname

                    else:
                        # target_path is a directory, file_name was not provided
                        # and content-disposition header was not found,
                        # so we'll use the last resort - the basename of the url
                        filename = os.path.basename(url)

                content_type = response.headers.get("content-type")

                # Prepare the target directory and the temporary file path

                target_path = os.path.join(directory, filename)
                temp_file_path = target_path + f".{uuid.uuid1().hex}.part"
                directory = os.path.dirname(temp_file_path)
                os.makedirs(directory, exist_ok=True)

                # Get the file size from the content-length headers
                # to track the download progress
                file_size = int(response.headers.get("content-length", 0))

                short_url = shorten_string(url, 50)
                logger.debug(f"Downloading {short_url} to {target_path}")

                async with aiofiles.open(temp_file_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        await f.write(chunk)
                        i += len(chunk)
                        await handle_progress(i)

        os.rename(temp_file_path, target_path)
        finfo_payload = {"filename": filename, "size": i}
        if content_type:
            finfo_payload["content_type"] = content_type
        return FileInfo(**finfo_payload)

    finally:
        if os.path.exists(temp_file_path):
            logger.debug(f"Removing temporary file: {temp_file_path}")
            os.remove(temp_file_path)
