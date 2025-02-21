import asyncio
import queue
import time
from typing import Any

from ayon_server.background.background_worker import BackgroundWorker
from ayon_server.config import ayonconfig
from ayon_server.events import EventStream
from ayon_server.logging import logger


class LogCollector(BackgroundWorker):
    """Log handler that collects log messages and dispatches them to the event stream.

    It is started as a background worker and runs in the background
    so it does not block the main loop.
    """

    def initialize(self):
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.start_time = time.time()
        logger.add(self, level=ayonconfig.log_level_db)

    def __call__(self, message):
        # We need to add messages to the queue even if the
        # collector is not running to catch the messages
        # that are logged during the startup.
        record = message.record
        if len(self.queue.queue) > 1000:
            return

        topic = f"log.{record['level'].name.lower()}"
        description = record["message"].splitlines()[0].strip()

        extra = dict(record["extra"])
        extra["file"] = record["file"].path
        extra["function"] = record["function"]
        extra["line"] = record["line"]

        user = extra.pop("user", None)
        project = record.pop("project", None)
        self.queue.put(
            {
                "topic": topic,
                "description": description,
                "user": user,
                "project": project,
                "payload": extra,
            }
        )

    async def process_message(self, record):
        try:
            await EventStream.dispatch(**record)
        except Exception:
            m = f"Unable to dispatch log message: {record}"
            # do not use the logger, if you don't like recursion
            print(m, flush=True)

    async def run(self):
        # During the startup, we cannot write to the database
        # so the following loop patiently waits for the database
        # to become ready.
        while True:
            try:
                await EventStream.dispatch("server.log_collector_started")
            except Exception:
                # Do not log the exception using the logger,
                # if you don't like recursion.
                await asyncio.sleep(0.5)
                continue
            break

        while True:
            if self.queue.empty():
                await asyncio.sleep(0.2)
                continue

            record = self.queue.get()
            await self.process_message(record)

    async def finalize(self):
        while not self.queue.empty():
            logger.debug(
                f"Processing {len(self.queue.queue)} remaining log messages",
                handlers=None,
                user="server",
            )
            record = self.queue.get()
            await self.process_message(record)


# Create the instance here.
# We are importing it first it in ayon_server.api.server
# - that initiates the collector and every consecutive log
# message will be added to the queue.
# Then we import it in background_workers - that puts it
# to the background worker class and starts dumping the
# messages to the database.

log_collector = LogCollector()
