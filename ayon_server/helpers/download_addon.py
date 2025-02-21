import hashlib

from ayon_server.constraints import Constraints
from ayon_server.events import EventStream
from ayon_server.exceptions import ForbiddenException
from ayon_server.installer import background_installer
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger


async def download_addon(
    url: str,
    addon_name: str | None = None,
    addon_version: str | None = None,
    *,
    no_queue: bool = False,
) -> str:
    if (
        allow_custom_addons := await Constraints.check("allowCustomAddons")
    ) is not None:
        if not allow_custom_addons:
            allowed_prefixes = ["https://download.ynput.cloud"]
            if not any(url.startswith(prefix) for prefix in allowed_prefixes):
                raise ForbiddenException("Custom addons uploads are not allowed")

    hash = hashlib.sha256(f"addon_install_{url}".encode()).hexdigest()

    query = """
        SELECT id FROM events
        WHERE topic = 'addon.install_from_url'
        AND hash = $1
    """

    summary = {"url": url}
    if addon_name and addon_version:
        summary["name"] = addon_name
        summary["version"] = addon_version

    res = await Postgres.fetch(query, hash)
    if res:
        event_id = res[0]["id"]
        await EventStream.update(
            event_id,
            description="Reinstalling addon from URL",
            summary=summary,
            status="pending",
        )
    else:
        event_id = await EventStream.dispatch(
            "addon.install_from_url",
            hash=hash,
            description="Installing addon from URL",
            summary=summary,
            finished=False,
        )

    url_label = url[:50]
    if url_label != url:
        url_label += "..."
    logger.debug(f"Downloading addon from {url_label}")
    if no_queue:
        await background_installer.process_event(event_id)
    else:
        await background_installer.enqueue(event_id)
    return event_id
