from ayon_server.lib.postgres import Postgres
from maintenance.maintenance_task import ProjectMaintenanceTask
from nxtools import logging


async def clear_activities(project_name: str) -> None:
    """Remove activities that no longer have a corresponding origin

    This can happen when an entity is deleted and there's no activity
    on that entity for a grace period (10 days).

    This does not remove files as their activity_id will be set to NULL
    and they will be deleted by the file clean-up.
    """
    GRACE_PERIOD = 10  # days

    for entity_type in ("folder", "task", "version", "workfile"):
        query = f"""

            -- Get the list of entities that were deleted
            -- in the last GRACE_PERIOD days

            WITH recently_deleted_entities AS (
                SELECT (summary->>'entityId')::UUID as entity_id
                FROM events
                WHERE topic = 'entity.{entity_type}.deleted'
                AND project_name = '{project_name}'
                AND updated_at > now() - interval '{GRACE_PERIOD} days'
                AND summary->>'entityId' IS NOT NULL
            ),

            -- Get the list of activities that were updated
            -- in the last GRACE_PERIOD days

            grace_period_entity_ids AS (
                SELECT entity_id FROM project_{project_name}.activity_references
                WHERE entity_id IS NOT NULL
                AND entity_type = '{entity_type}'
                AND updated_at > now()  - interval '{GRACE_PERIOD} days'
            ),

            -- Find activities that reference entities that no longer exist
            -- and were not updated during the grace period
            -- and the entity were not deleted during the grace period

            deletable_activities AS (
                SELECT DISTINCT ar.activity_id
                FROM project_{project_name}.activity_references ar
                LEFT JOIN grace_period_entity_ids gp ON ar.entity_id = gp.entity_id
                LEFT JOIN recently_deleted_entities rd ON ar.entity_id = rd.entity_id
                LEFT JOIN project_{project_name}.{entity_type}s e ON ar.entity_id = e.id
                WHERE ar.entity_id IS NOT NULL
                  AND ar.reference_type = 'origin'
                  AND ar.entity_type = '{entity_type}'
                  AND gp.entity_id IS NULL
                  AND rd.entity_id IS NULL
                  AND e.id IS NULL
            ),

            -- Delete the activities and return the ids

            deleted_activities AS (
                DELETE FROM project_{project_name}.activities
                WHERE id IN (SELECT activity_id FROM deletable_activities)
                RETURNING id
            )

            -- Return the count of deleted activities

            SELECT count(*) as deleted FROM deleted_activities
        """

        res = await Postgres.fetch(query, timeout=10)
        count = res[0]["deleted"]

        if count:
            logging.debug(
                f"Deleted {count} orphaned "
                f"{entity_type} activities from {project_name}"
            )


class RemoveUnusedActivities(ProjectMaintenanceTask):
    description = "Removing old activities"

    async def main(self, project_name: str):
        await clear_activities(project_name)
