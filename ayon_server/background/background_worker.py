import asyncio

from ayon_server.logging import log_traceback, logger


class BackgroundWorker:
    def __init__(self):
        self.task: asyncio.Task[None] | None = None
        self.shutting_down = False
        self.initialize()

    def initialize(self):
        pass

    def start(self):
        logger.debug(f"Starting background worker {self.__class__.__name__}")
        self.task = asyncio.create_task(self._run())

    async def shutdown(self):
        if self.task:
            self.task.cancel()

        self.shutting_down = True
        while self.is_running:
            logger.debug(f"Waiting for {self.__class__.__name__} to stop", handlers=[])
            await asyncio.sleep(0.1)
        logger.debug(f"{self.__class__.__name__} stopped", handlers=[])

    @property
    def is_running(self):
        return self.task and not self.task.done()

    async def _run(self) -> None:
        try:
            await self.run()
        except asyncio.CancelledError:
            logger.debug(f"{self.__class__.__name__} is cancelled", handlers=[])
            self.shutting_down = True
        except Exception:
            log_traceback(handlers=[])
        finally:
            await self.finalize()
            self.task = None

        if not self.shutting_down:
            logger.debug("Restarting", self.__class__.__name__, handlers=[])
            self.start()

    async def run(self):
        pass

    async def finalize(self):
        pass
