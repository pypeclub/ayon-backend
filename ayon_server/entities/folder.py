from datetime import datetime
from typing import Any

from ayon_server.access.utils import ensure_entity_access
from ayon_server.entities.core import ProjectLevelEntity, attribute_library
from ayon_server.entities.models import ModelSet
from ayon_server.exceptions import (
    AyonException,
    ForbiddenException,
    NotFoundException,
)
from ayon_server.helpers.hierarchy_cache import rebuild_hierarchy_cache
from ayon_server.helpers.inherited_attributes import rebuild_inherited_attributes
from ayon_server.lib.postgres import Connection, Postgres
from ayon_server.types import ProjectLevelEntityType
from ayon_server.utils import EntityID, SQLTool, dict_exclude
from nxtools import logging


class FolderEntity(ProjectLevelEntity):
    entity_type: ProjectLevelEntityType = "folder"
    model: ModelSet = ModelSet("folder", attribute_library["folder"])

    @classmethod
    async def load(
        cls,
        project_name: str,
        entity_id: str,
        transaction: Connection | None = None,
        for_update: bool = False,
    ) -> "FolderEntity":
        """Load a folder from the database by its project name and IDself.

        This is reimplemented, because we need to select dynamic
        attribute hierarchy.path along with the base data and
        the attributes inherited from parent entities.
        """

        if EntityID.parse(entity_id) is None:
            raise ValueError(f"Invalid {cls.entity_type} ID specified")

        query = f"""
            SELECT
                f.id as id,
                f.name as name,
                f.label as label,
                f.folder_type as folder_type,
                f.parent_id as parent_id,
                f.thumbnail_id as thumbnail_id,
                f.attrib as attrib,
                f.data as data,
                f.active as active,
                f.created_at as created_at,
                f.updated_at as updated_at,
                f.status as status,
                f.tags as tags,
                h.path as path,
                ia.attrib AS inherited_attrib,
                p.attrib AS project_attrib
            FROM project_{project_name}.folders as f
            INNER JOIN
                project_{project_name}.hierarchy as h
                ON f.id = h.id
            LEFT JOIN
                project_{project_name}.exported_attributes as ia
                ON f.parent_id = ia.folder_id
            INNER JOIN public.projects as p
                ON p.name ILIKE $2
            WHERE f.id=$1
            {'FOR UPDATE OF f'
                if transaction and for_update else ''
            }
            """

        try:
            async for record in Postgres.iterate(query, entity_id, project_name):
                record = dict(record)
                path = record.pop("path")
                if path is not None:
                    # ensure path starts with / but does not end with /
                    record["path"] = f"/{path.strip('/')}"
                attrib: dict[str, Any] = {}

                for key, value in record.get("project_attrib", {}).items():
                    if key in attribute_library.inheritable_attributes():
                        attrib[key] = value

                if (ia := record["inherited_attrib"]) is not None:
                    for key, value in ia.items():
                        if key in attribute_library.inheritable_attributes():
                            attrib[key] = value

                elif record["parent_id"] is not None:
                    logging.warning(
                        f"Folder {record['path']} does not have inherited attributes."
                        "this shouldn't happen"
                    )
                attrib.update(record["attrib"])
                own_attrib = list(record["attrib"].keys())
                payload = {**record, "attrib": attrib}
                return cls.from_record(
                    project_name=project_name,
                    payload=payload,
                    own_attrib=own_attrib,
                )
        except Postgres.UndefinedTableError:
            raise NotFoundException(f"Project {project_name} not found")
        raise NotFoundException("Entity not found")

    async def save(self, transaction: Connection | None = None) -> None:
        if not transaction:
            async with Postgres.acquire() as conn, conn.transaction():
                await self._save(conn)
                await self.commit(conn)
        else:
            await self._save(transaction)

    async def _save(self, transaction: Connection) -> None:
        """Save the folder to the database.

        This overriden method also clears exported_attributes,
        which is then repopulated during commit.
        """

        if self.status is None:
            self.status = await self.get_default_status()

        if self.folder_type is None:
            res = await transaction.fetch(
                f"""
                SELECT name from project_{self.project_name}.folder_types
                ORDER BY position ASC LIMIT 1
                """
            )
            if not res:
                raise AyonException("No folder types defined")
            self.folder_type = res[0]["name"]

        attrib = {}
        for key in self.own_attrib:
            if not hasattr(self.attrib, key):
                continue
            if (value := getattr(self.attrib, key)) is not None:
                attrib[key] = value

        if self.exists:
            # Update existing entity

            await transaction.execute(
                *SQLTool.update(
                    f"project_{self.project_name}.{self.entity_type}s",
                    f"WHERE id = '{self.id}'",
                    name=self.name,
                    label=self.label,
                    folder_type=self.folder_type,
                    parent_id=self.parent_id,
                    thumbnail_id=self.thumbnail_id,
                    status=self.status,
                    tags=self.tags,
                    attrib=attrib,
                    data=self.data,
                    active=self.active,
                    updated_at=datetime.now(),
                )
            )

        else:
            # Create a new entity
            await transaction.execute(
                *SQLTool.insert(
                    f"project_{self.project_name}.{self.entity_type}s",
                    **dict_exclude(self.dict(exclude_none=True), ["own_attrib"]),
                )
            )

    async def commit(self, transaction: Connection | None = None) -> None:
        """Refresh hierarchy materialized view on folder save."""

        async def _commit(conn):
            await conn.execute(
                f"""
                REFRESH MATERIALIZED VIEW CONCURRENTLY
                project_{self.project_name}.hierarchy
                """
            )
            await rebuild_inherited_attributes(self.project_name, transaction=conn)
            await rebuild_hierarchy_cache(self.project_name, transaction=conn)

        if transaction is not None:
            await _commit(transaction)
            return
        else:
            async with Postgres.acquire() as conn, conn.transaction():
                await _commit(conn)

    async def delete(self, transaction: Connection | None = None, **kwargs) -> bool:
        if not transaction:
            async with Postgres.acquire() as conn, conn.transaction():
                return await self._delete(conn, **kwargs)
        else:
            return await self._delete(transaction, **kwargs)

    async def _delete(self, transaction: Connection, **kwargs) -> bool:
        if kwargs.get("force", False):
            logging.info(f"Force deleting folder and all its children. {self.path}")
            await transaction.execute(
                f"""
                DELETE FROM project_{self.project_name}.products
                WHERE folder_id IN (
                    SELECT id FROM project_{self.project_name}.hierarchy
                    WHERE path = $1
                    OR path LIKE $1 || '/%'
                ) RETURNING name
                """,
                self.path.lstrip("/"),
            )

        res = await super().delete(transaction=transaction, **kwargs)
        if res:
            await rebuild_hierarchy_cache(self.project_name, transaction=transaction)
        return res

    async def get_versions(self, transaction: Connection | None = None) -> list[str]:
        """Return of version ids associated with this folder."""
        query = f"""
            SELECT v.id as version_id
            FROM project_{self.project_name}.versions as v
            INNER JOIN project_{self.project_name}.products as s
                ON s.id = v.product_id
            WHERE s.folder_id = $1
            """
        return [row["version_id"] async for row in Postgres.iterate(query, self.id)]

    async def ensure_create_access(self, user, **kwargs) -> None:
        """Check if the user has access to create a new entity.

        Raises FobiddenException if the user does not have access.
        Reimplements the method from the parent class, because in
        case of folders we need to check the parent folder.
        """
        try:
            if self.parent_id is None:
                # if user can create a project, they can create a root folders
                user.check_permissions("studio.create_projects")
        except ForbiddenException:
            pass
        else:
            return

        await ensure_entity_access(
            user,
            self.project_name,
            self.entity_type,
            self.parent_id,
            "create",
        )

    async def ensure_update_access(self, user, **kwargs) -> None:
        """Check if the user has access to update the folder.

        Raises FobiddenException if the user does not have access.
        """

        if user.is_manager:
            return

        # if only thumbnail is updated, check publish access,
        # which is less restrictive than update access and it is
        # good enough for thumbnail updates
        if kwargs.get("thumbnail_only"):
            try:
                await ensure_entity_access(
                    user, self.project_name, self.entity_type, self.id, "publish"
                )
            except ForbiddenException:
                pass
            else:
                return

        await ensure_entity_access(
            user, self.project_name, self.entity_type, self.id, "update"
        )

    #
    # Properties
    #

    @property
    def label(self) -> str | None:
        """Return the label of the folder."""
        return self._payload.label  # type: ignore

    @label.setter
    def label(self, value):
        """Set the label of the folder."""
        self._payload.label = value  # type: ignore

    @property
    def parent_id(self) -> str | None:
        return self._payload.parent_id  # type: ignore

    @parent_id.setter
    def parent_id(self, value: str) -> None:
        self._payload.parent_id = value  # type: ignore

    @property
    def folder_type(self) -> str | None:
        return self._payload.folder_type  # type: ignore

    @folder_type.setter
    def folder_type(self, value: str) -> None:
        self._payload.folder_type = value  # type: ignore

    @property
    def thumbnail_id(self) -> str | None:
        return self._payload.thumbnail_id  # type: ignore

    @thumbnail_id.setter
    def thumbnail_id(self, value: str) -> None:
        self._payload.thumbnail_id = value  # type: ignore

    #
    # Read only properties
    #

    @property
    def path(self) -> str:
        return self._payload.path  # type: ignore

    @property
    def entity_subtype(self) -> str | None:
        return self.folder_type
