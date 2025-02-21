import asyncio

from ayon_server.background.background_worker import BackgroundWorker
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import log_traceback


class InvalidateActions(BackgroundWorker):
    """Purge unprocessed launcher actions.

    If an action remains in pending state for more than 10 minutes,
    it is considered stale and is deleted. Normally, launcher should
    take action on the event within a few seconds or minutes.
    """

    async def run(self):
        # Execute the first clean-up after a minue,
        # when everything is settled after the start-up.
        # We don't need to clean up immediately after the start-up.

        await asyncio.sleep(60)

        while True:
            await self.invalidate()
            await asyncio.sleep(180)

    async def invalidate(self) -> None:
        query = """
            DELETE FROM events WHERE topic = 'action.launcher'
            AND status = 'pending'
            AND created_at < now() - interval '5 minutes'
        """
        try:
            await Postgres.execute(query)
        except Exception:
            log_traceback("Invalidating actions failed")


invalidate_actions = InvalidateActions()
