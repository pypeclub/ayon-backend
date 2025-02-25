from typing import Any, overload

from ayon_server.entities.user import UserEntity
from ayon_server.exceptions import ForbiddenException, NotFoundException
from ayon_server.lib.postgres import Connection, Postgres

from .models import EntityListItemModel, EntityListModel


class EntityList:
    project_name: str
    payload: EntityListModel
    user: UserEntity | None = None
    exists: bool = False

    @overload
    def __init__(self, project_name: str, payload: dict[str, Any]) -> None: ...

    @overload
    def __init__(self, project_name: str, payload: EntityListModel) -> None: ...

    def __init__(
        self, project_name: str, payload: dict[str, Any] | EntityListModel
    ) -> None:
        if isinstance(payload, dict):
            self.payload = EntityListModel(**payload)
        else:
            self.payload = payload
        self.project_name = project_name

    @classmethod
    async def _load(
        cls,
        project_name: str,
        id: str,
        *,
        user: UserEntity | None,
        conn: Connection,
    ) -> "EntityList":
        res = await conn.fetch(
            f"""
            SELECT * FROM project_{project_name}.entity_lists
            WHERE id = $1
            """,
            id,
        )

        if not res:
            raise NotFoundException("Entity list not found")

        entity_list = EntityListModel(**res[0])

        if user is not None:
            # Check user access to the list

            if entity_list.access.get(user.name, 0) < 10:
                raise ForbiddenException("User does not have access to this list")

        q = f"""
            SELECT * FROM project_{project_name}.entity_list_items
            WHERE list_id = $1
            ORDER BY position
            """

        async for row in Postgres.iterate(q, id):
            list_item = EntityListItemModel(**row)
            entity_list.items.append(list_item)

        result = cls(project_name, entity_list)
        result.user = user
        result.exists = True

    @classmethod
    async def load(
        cls,
        project_name: str,
        id: str,
        *,
        user: UserEntity | None = None,
        conn: Connection | None = None,
    ) -> "EntityList":
        if conn is None:
            async with Postgres.acquire() as c, c.transaction():
                return await cls._load(project_name, id, conn=c, user=user)
        else:
            return await cls._load(project_name, id, conn=conn, user=user)

    async def save(
        self,
        *,
        user: UserEntity | None = None,
        conn: Connection | None = None,
    ) -> None:
        user = user or self.user
