import time

from ayon_server.config import ayonconfig
from ayon_server.lib.postgres import Postgres
from maintenance.maintenance_task import StudioMaintenanceTask
from nxtools import logging


async def clear_events() -> None:
    """Purge old events.

    Delete events older than the value specified in ayon-config.
    This is opt-in and by default, old events are not deleted.
    """

    if ayonconfig.event_retention_days is None:
        return

    num_days = ayonconfig.event_retention_days

    while True:
        start_time = time.monotonic()
        res = await Postgres.fetch(
            f"""
            WITH blocked_events AS (
                SELECT DISTINCT(depends_on) as id FROM events
                WHERE depends_on IS NOT NULL
            ),

            deletable_events AS (
                SELECT id
                FROM events
                WHERE updated_at < now() - interval '{num_days} days'
                AND id NOT IN (SELECT id FROM blocked_events)
                ORDER BY updated_at ASC
                LIMIT 5000
            ),

            deleted_events AS(
                DELETE FROM events
                WHERE id IN (SELECT id FROM deletable_events)
                RETURNING id as deleted
            )

            SELECT count(*) as deleted FROM deleted_events;

            """
        )
        deleted = res[0]["deleted"]
        if deleted:
            logging.debug(
                f"Deleted {deleted} old events"
                f" in {time.monotonic() - start_time:.2f} seconds"
            )
        else:
            break


class RemoveOldEvents(StudioMaintenanceTask):
    description = "Removing old events"

    async def main(self):
        await clear_events()
