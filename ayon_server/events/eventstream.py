from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from ayon_server.exceptions import ConstraintViolationException, NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.utils import SQLTool, json_dumps

from .base import EventModel, EventStatus, create_id

HandlerType = Callable[[EventModel], Awaitable[None]]


class EventStream:
    model: type[EventModel] = EventModel
    local_hooks: dict[str, dict[str, HandlerType]] = {}
    global_hooks: dict[str, dict[str, HandlerType]] = {}

    @classmethod
    def subscribe(
        cls, topic: str, handler: HandlerType, all_nodes: bool = False
    ) -> str:
        token = create_id()
        hooks = cls.global_hooks if all_nodes else cls.local_hooks
        topic_hooks = hooks.setdefault(topic, {})
        topic_hooks[token] = handler
        return token

    @classmethod
    def unsubscribe(cls, token: str) -> None:
        for hooks in (cls.global_hooks, cls.local_hooks):
            for topic, mapping in tuple(hooks.items()):
                mapping.pop(token, None)
                if not mapping:
                    hooks.pop(topic)

    @classmethod
    async def dispatch(
        cls,
        topic: str,
        *,
        sender: str | None = None,
        sender_type: str | None = None,
        hash: str | None = None,
        project: str | None = None,
        user: str | None = None,
        depends_on: str | None = None,
        description: str | None = None,
        summary: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        finished: bool = True,
        store: bool = True,
        reuse: bool = False,
        recipients: list[str] | None = None,
    ) -> str:
        """

        finished:
            whether the event one shot and should be marked as finished upon creation

        store:
            whether to store the event in the database

        reuse:
            allow to reuse an existing event with the same hash

        recipients:
            list of user names to notify via websocket (None for all users)
        """
        if summary is None:
            summary = {}
        if payload is None:
            payload = {}
        if description is None:
            description = ""

        event_id = create_id()
        if hash is None:
            hash = f"{event_id}"

        status: str = "finished" if finished else "pending"
        progress: float = 100 if finished else 0.0

        event = EventModel(
            id=event_id,
            hash=hash,
            sender=sender,
            sender_type=sender_type,
            topic=topic,
            project=project,
            user=user,
            depends_on=depends_on,
            status=status,
            description=description,
            summary=summary,
            payload=payload,
            retries=0,
        )

        if store:
            query = """
                INSERT INTO
                events (
                    id,
                    hash,
                    sender,
                    sender_type,
                    topic,
                    project_name,
                    user_name,
                    depends_on,
                    status,
                    description,
                    summary,
                    payload
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """
            if reuse:
                query += """
                    ON CONFLICT (hash) DO UPDATE SET
                        id = EXCLUDED.id,
                        sender = EXCLUDED.sender,
                        sender_type = EXCLUDED.sender_type,
                        topic = EXCLUDED.topic,
                        project_name = EXCLUDED.project_name,
                        user_name = EXCLUDED.user_name,
                        depends_on = EXCLUDED.depends_on,
                        status = EXCLUDED.status,
                        description = EXCLUDED.description,
                        summary = EXCLUDED.summary,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                """
            else:
                query += "ON CONFLICT (hash) DO NOTHING"

            try:
                res = await Postgres.execute(
                    query,
                    event.id,
                    event.hash,
                    event.sender,
                    event.sender_type,
                    event.topic,
                    event.project,
                    event.user,
                    event.depends_on,
                    status,
                    description,
                    event.summary,
                    event.payload,
                )
            except Postgres.ForeignKeyViolationError as e:
                raise ConstraintViolationException(
                    "Event depends on non-existing event",
                ) from e

            except Postgres.UniqueViolationError as e:
                raise ConstraintViolationException(
                    "Unable to reuse the event. Another event depends on it",
                ) from e

            if (not reuse) and res == "INSERT 0 0":
                raise ConstraintViolationException(
                    "Event with the same hash already exists",
                )

        depends_on = (
            str(event.depends_on).replace("-", "") if event.depends_on else None
        )
        await Redis.publish(
            json_dumps(
                {
                    "id": str(event.id).replace("-", ""),
                    "topic": event.topic,
                    "project": event.project,
                    "user": event.user,
                    "dependsOn": depends_on,
                    "description": event.description,
                    "summary": event.summary,
                    "status": event.status,
                    "progress": progress,
                    "sender": sender,
                    "senderType": sender_type,
                    "store": store,  # useful to allow querying details
                    "recipients": recipients,
                    "createdAt": event.created_at,
                    "updatedAt": event.updated_at,
                }
            )
        )

        handlers = cls.local_hooks.get(event.topic, {}).values()
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.debug(f"Error in event handler: {e}")

        return event.id

    @classmethod
    async def update(
        cls,
        event_id: str,
        *,
        sender: str | None = None,
        sender_type: str | None = None,
        project: str | None = None,
        user: str | None = None,
        status: EventStatus | None = None,
        description: str | None = None,
        summary: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        progress: float | None = None,
        store: bool = True,
        retries: int | None = None,
        recipients: list[str] | None = None,
    ) -> bool:
        new_data: dict[str, Any] = {"updated_at": datetime.now()}

        if sender is not None:
            new_data["sender"] = sender
        if sender_type is not None:
            new_data["sender_type"] = sender_type
        if project is not None:
            new_data["project_name"] = project
        if status is not None:
            new_data["status"] = status
        if description is not None:
            new_data["description"] = description
        if summary is not None:
            new_data["summary"] = summary
        if payload is not None:
            new_data["payload"] = payload
        if retries is not None:
            new_data["retries"] = retries
        if user is not None:
            new_data["user_name"] = user

        if store:
            query = SQLTool.update("events", f"WHERE id = '{event_id}'", **new_data)

            query[0] = (
                query[0]
                + """
                 RETURNING
                    id,
                    topic,
                    project_name,
                    user_name,
                    depends_on,
                    description,
                    summary,
                    status,
                    sender,
                    sender_type,
                    created_at,
                    updated_at
            """
            )

        else:
            query = ["SELECT * FROM events WHERE id=$1", event_id]

        result = await Postgres.fetch(*query)
        for row in result:
            data = dict(row)
            if not store:
                data.update(new_data)
            message = {
                "id": data["id"],
                "topic": data["topic"],
                "project": data["project_name"],
                "user": data["user_name"],
                "dependsOn": data["depends_on"],
                "description": data["description"],
                "summary": data["summary"],
                "status": data["status"],
                "sender": data["sender"],
                "senderType": data["sender_type"],
                "recipients": recipients,
                "createdAt": data["created_at"],
                "updatedAt": data["updated_at"],
            }
            if progress is not None:
                message["progress"] = progress
            await Redis.publish(json_dumps(message))
            return True
        return False

    @classmethod
    async def get(cls, event_id: str) -> EventModel:
        query = "SELECT * FROM events WHERE id = $1", event_id
        event: EventModel | None = None
        async for record in Postgres.iterate(*query):
            event = EventModel(
                id=record["id"],
                hash=record["hash"],
                topic=record["topic"],
                project=record["project_name"],
                user=record["user_name"],
                sender=record["sender"],
                sender_type=record["sender_type"],
                depends_on=record["depends_on"],
                status=record["status"],
                retries=record["retries"],
                description=record["description"],
                payload=record["payload"],
                summary=record["summary"],
                created_at=record["created_at"],
                updated_at=record["updated_at"],
            )
            break

        if event is None:
            raise NotFoundException("Event not found")
        return event

    @classmethod
    async def delete(cls, event_id: str) -> None:
        await Postgres.execute("DELETE FROM events WHERE id = $1", event_id)
        return None
