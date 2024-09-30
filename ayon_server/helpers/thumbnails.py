import base64
import functools
import io

from nxtools import logging
from PIL import Image
from starlette.concurrency import run_in_threadpool

MAX_THUMBNAIL_WIDTH = 600
MAX_THUMBNAIL_HEIGHT = 600


class ThumbnailProcessNoop(Exception):
    pass


def calculate_scaled_size(
    source_width: int,
    source_height: int,
    max_width: int | None,
    max_height: int | None,
) -> tuple[int, int]:
    """
    Calculate the scaled size for an image while maintaining the aspect ratio.

    Parameters:
    source_width (int): The width of the original image.
    source_height (int): The height of the original image.
    max_width (int | None): The maximum allowed width for the scaled image.
    max_height (int | None): The maximum allowed height for the scaled image.

    Returns:
    tuple[int, int]: A tuple containing the scaled width and height.
    """
    aspect_ratio = source_width / source_height

    if max_width is None and max_height is None:
        raise ValueError("At least one of max_width or max_height must be specified")

    if not (max_width or max_height):
        raise ValueError("At least one of max_width or max_height must be specified")

    if max_width is None:
        assert max_height
        max_width = int(max_height * aspect_ratio)
    elif max_height is None:
        assert max_width
        max_height = int(max_width / aspect_ratio)

    if source_width <= max_width and source_height <= max_height:
        return source_width, source_height

    if (max_width / aspect_ratio) <= max_height:
        target_width = max_width
        target_height = int(max_width / aspect_ratio)
    else:
        target_height = max_height
        target_width = int(max_height * aspect_ratio)

    if target_width % 2 != 0:
        target_width -= 1
    if target_height % 2 != 0:
        target_height -= 1

    return target_width, target_height


async def process_thumbnail(
    image_bytes: bytes,
    size: tuple[int | None, int | None] = (150, None),
    format: str | None = None,
    raise_on_noop: bool = False,
) -> bytes:
    """
    Resize an image to the specified dimensions and format asynchronously.

    Parameters:
    image_bytes: Byte stream of the original image.
    size: Desired (width, height) of the thumbnail.
        If one of the dimensions is None,
        it will be calculated based on the aspect ratio
    format: Desired image format (e.g., 'JPEG', 'PNG').
        If None, retains the original format.

    Returns:
    bytes: Byte stream of the resized and potentially reformatted image.

    Raises:
    ValueError: If both dimensions in size are None.
    """

    def process_image():
        with Image.open(io.BytesIO(image_bytes)) as img:
            target_format = format or img.format or "JPEG"

            # Ensure that we have valid dimensions
            if size == (None, None):
                raise ValueError("Both width and height cannot be None")

            original_width, original_height = img.size

            new_width, new_height = calculate_scaled_size(
                original_width, original_height, *size
            )

            if new_width >= original_width or new_height >= original_height:
                # If the requested size is larger than the original image,
                # return the original image
                if raise_on_noop:
                    raise ThumbnailProcessNoop()
                return image_bytes

            logging.debug(
                f"Resizing image from {img.size} to {(new_width, new_height)}"
            )
            img = img.resize((new_width, new_height), Image.LANCZOS)  # type: ignore
            img_byte_arr = io.BytesIO()

            # Adjustments for specific formats
            if target_format == "JPEG":
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(img_byte_arr, format=target_format, optimize=True, quality=85)
            else:
                img.save(img_byte_arr, format=target_format)

            return img_byte_arr.getvalue()

    # Run the blocking image processing in a separate thread
    normalized_bytes = await run_in_threadpool(process_image)
    return normalized_bytes


@functools.cache
def get_fake_thumbnail() -> bytes:
    """Returns a fake thumbnail image as a byte stream.

    The image is a 1x1 pixel PNG encoded in base64.
    """
    base64_string = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="  # noqa
    return base64.b64decode(base64_string)
