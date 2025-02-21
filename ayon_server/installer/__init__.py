import asyncio

from ayon_server.api.system import require_server_restart
from ayon_server.background.background_worker import BackgroundWorker
from ayon_server.events import EventStream
from ayon_server.installer.addons import install_addon_from_url, unpack_addon
from ayon_server.installer.dependency_packages import download_dependency_package
from ayon_server.installer.installers import download_installer
from ayon_server.lib.postgres import Postgres
from nxtools import log_traceback, logging

from .addons import AddonZipInfo

TOPICS = [
    "addon.install",
    "addon.install_from_url",
    "dependency_package.install_from_url",
    "installer.install_from_url",
]


class TooManyRetries(Exception):
    pass


async def handle_need_restart(installer: "BackgroundInstaller") -> None:
    await asyncio.sleep(1)
    if installer.event_queue.empty() and installer.restart_needed:
        await require_server_restart(
            None, "Restart the server to apply the addon changes."
        )


class BackgroundInstaller(BackgroundWorker):
    def initialize(self) -> None:
        self.event_queue: asyncio.Queue[str] = asyncio.Queue()
        self.restart_needed: bool = False

    async def enqueue(self, event_id: str) -> None:
        logging.debug(f"Background installer: enqueuing event {event_id}")
        await self.event_queue.put(event_id)

    async def process_event(self, event_id: str) -> None:
        res = await Postgres().fetch(
            " SELECT topic, status, summary, retries FROM events WHERE id = $1 ",
            event_id,
        )

        if not res:
            return

        if res[0]["status"] == "failed" and res[0]["retries"] > 3:
            logging.error(f"Event {event_id} failed too many times")
            raise TooManyRetries()

        topic = res[0]["topic"]
        summary = res[0]["summary"]

        logging.info(f"Background installer: processing {topic} event: {event_id}")

        if topic == "addon.install":
            await unpack_addon(
                event_id,
                AddonZipInfo(**summary),
            )
            self.restart_needed = True

        elif topic == "addon.install_from_url":
            await install_addon_from_url(event_id, summary["url"])
            self.restart_needed = True

        elif topic == "dependency_package.install_from_url":
            await download_dependency_package(event_id, summary["url"])

        elif topic == "installer.install_from_url":
            await download_installer(event_id, summary["url"])

        logging.info(
            f"Background installer: finished processing {topic} event: {event_id}"
        )

        asyncio.create_task(handle_need_restart(self))

    async def run(self) -> None:
        # load past unprocessed events
        res = await Postgres().fetch(
            """
            SELECT id FROM events
            WHERE topic = ANY($1) AND status NOT IN ('finished', 'failed')
            ORDER BY created_at ASC
            """,
            TOPICS,
        )

        for row in res:
            await self.enqueue(row["id"])

        while True:
            event_id = await self.event_queue.get()
            try:
                await self.process_event(event_id)
            except TooManyRetries:
                pass
            except Exception as e:
                log_traceback(f"Error while processing event {event_id}: {e}")
                r = await Postgres.fetch(
                    "SELECT retries FROM events WHERE id = $1", event_id
                )
                await EventStream.update(
                    event_id,
                    status="failed",
                    description=f"Failed to process event: {e}",
                    retries=r[0]["retries"] + 1,
                )
                await self.enqueue(event_id)


background_installer = BackgroundInstaller()
