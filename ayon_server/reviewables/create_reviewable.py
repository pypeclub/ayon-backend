from ayon_server.activities.create_activity import create_activity
from ayon_server.activities.watchers.set_watchers import ensure_watching
from ayon_server.entities import UserEntity, VersionEntity
from ayon_server.events import EventStream
from ayon_server.exceptions import BadRequestException
from ayon_server.files import Storages, create_project_file_record
from ayon_server.helpers.ffprobe import availability_from_media_info
from ayon_server.reviewables.models import ReviewableAuthor, ReviewableModel
from nxtools import logging


def check_valid_mime(content_type: str) -> None:
    """Checks if the content type is valid for reviewables."""
    # TODO: replace with helpers.mimetypes functions

    if content_type.lower().startswith("video/"):
        return None
    if content_type.lower() in ["application/mxf"]:
        return None
    if content_type.lower().startswith("image/"):
        return None
    raise BadRequestException("Only videos are supported for reviewables now")


async def create_reviewable(
    version: VersionEntity,
    *,
    file_name: str,
    file_id: str,
    size: int,
    content_type: str,
    label: str | None = None,
    activity_id: str | None = None,
    user_name: str | None = None,
    sender: str | None = None,
    sender_type: str | None = None,
) -> ReviewableModel:
    """Creates a reviewable for a given version.

    The reviewable is created from a file that was uploaded,
    but its record was not yet created in the database!

    This is very very important!
    """

    project_name = version.project_name
    storage = await Storages.project(project_name)
    media_info = await storage.extract_media_info(file_id)

    check_valid_mime(content_type)

    if not media_info:
        logging.warning(f"Failed to extract media info for {file_name}")
        try:
            await storage.unlink(file_id)
        except Exception:
            pass
        raise BadRequestException("Failed to extract media info")

    await create_project_file_record(
        project_name,
        file_name,
        size=size,
        content_type=content_type,
        additional_data={"mediaInfo": media_info},
        file_id=file_id,
        user_name=user_name,
    )

    # Create activity

    body = f"""Uploaded a reviewable '{label or file_name}'"""

    activity_id = await create_activity(
        version,
        "reviewable",
        body=body,
        files=[file_id],
        data={"reviewableLabel": label},
        user_name=user_name,
    )

    summary = {
        "fileId": file_id,
        "versionId": version.id,
        "productId": version.product_id,
        "activityId": activity_id,
        "size": size,
        "filename": file_name,
        "label": label,
        "mimetype": content_type,
    }

    await EventStream.dispatch(
        "reviewable.created",
        sender=sender,
        sender_type=sender_type,
        user=user_name,
        project=project_name,
        summary=summary,
        description=f"Reviewable '{file_name}' uploaded",
    )

    availability = availability_from_media_info(media_info)

    if availability in ["unknown", "ready"]:
        processing = None
    else:
        processing = None
        # if not await is_transcoder_available():
        #     processing = None
        # else:
        #     processing = ReviewableProcessingStatus(
        #         event_id=None,
        #         status="enqueued",
        #         description="In a transcoder queue",
        #     )

    if user_name:
        user = await UserEntity.load(user_name)
        await ensure_watching(version, user)
        author = ReviewableAuthor(name=user.name, full_name=user.attrib.fullName)
    else:
        author = ReviewableAuthor(name="system", full_name=None)

    return ReviewableModel(
        file_id=file_id,
        activity_id=activity_id,
        filename=file_name,
        label=label,
        mimetype=content_type,
        availability=availability,
        media_info=media_info,
        created_from=None,
        processing=processing,
        author=author,
    )
