from contextlib import suppress
from typing import Annotated, Any

from asyncpg.exceptions import IntegrityConstraintViolationError

from ayon_server.api.postgres_exceptions import parse_postgres_exception
from ayon_server.config import ayonconfig
from ayon_server.entities import UserEntity
from ayon_server.entities.core import ProjectLevelEntity
from ayon_server.events import EventStream
from ayon_server.events.patch import build_pl_entity_change_events
from ayon_server.exceptions import (
    AyonException,
    BadRequestException,
    ConflictException,
    ForbiddenException,
)
from ayon_server.helpers.get_entity_class import get_entity_class
from ayon_server.lib.postgres import Connection, Postgres
from ayon_server.logging import logger
from ayon_server.types import Field, OPModel, ProjectLevelEntityType
from ayon_server.utils import create_uuid

from .common import OperationType, RollbackException

#
# Models
#


class OperationModel(OPModel):
    """Model for a single project-level operation.

    Operation type is one of create, update, delete.

    Each operation has a unique ID, that may be used to match the result
    in the response. The ID is automatically generated if not provided.

    The entity ID is required for update and delete operations, optional for create.
    The data field is required for create and update operations, ignored for delete.

    Force flag may be used for delete operations to force
    recursive deletion of the children entities (if applicable).
    """

    id: Annotated[str, Field(default_factory=create_uuid, title="Operation ID")]
    type: Annotated[OperationType, Field(title="Operation type")]
    entity_type: Annotated[ProjectLevelEntityType, Field(title="Entity type")]
    entity_id: Annotated[str | None, Field(title="Entity ID")] = None
    data: Annotated[dict[str, Any] | None, Field(title="Data")] = None
    force: Annotated[bool, Field(title="Force recursive deletion")] = False
    as_user: str | None = None


class OperationResponseModel(OPModel):
    """Response model for a single operation.

    Entity ID is `None` if the operation is a create and the operation fails.
    Status returns HTTP-like status code (201, 204, 404, etc.)
    """

    id: Annotated[str, Field(title="Operation ID")]
    type: Annotated[OperationType, Field(title="Operation type")]
    entity_type: Annotated[ProjectLevelEntityType, Field(title="Entity type")]
    entity_id: Annotated[str | None, Field(title="Entity ID")] = None
    success: Annotated[bool, Field(title="Operation success")]
    status: Annotated[int, Field(title="HTTP-like status code")]
    detail: Annotated[str | None, Field(title="Error message")] = None


class OperationsResponseModel(OPModel):
    operations: Annotated[list[OperationResponseModel], Field(default_factory=list)]
    success: Annotated[bool, Field(title="Overall success")]


#
# Processing
#


async def _process_operation(
    project_name: str,
    user: UserEntity | None,
    operation: OperationModel,
    transaction: Connection | None = None,
) -> tuple[ProjectLevelEntity, list[dict[str, Any]] | None, OperationResponseModel]:
    """Process a single operation. Raise an exception on error."""

    entity_class = get_entity_class(operation.entity_type)

    addr = f"{project_name}/{operation.entity_type}/{operation.entity_id}"
    logger.debug(
        f"ProjectLevelOperation: [{operation.type.upper()}] {addr}",
        project=project_name,
        operation_id=operation.id,
    )

    # Data for the event triggered after successful operation
    events: list[dict[str, Any]] | None = None
    status = 200

    if operation.type == "create":
        assert operation.data is not None, "data is required for create"
        payload = entity_class.model.post_model(**operation.data)
        payload_dict = payload.dict()
        if operation.entity_id is not None:
            payload_dict["id"] = operation.entity_id
        if operation.entity_type == "version":
            if user and not payload_dict.get("author"):
                payload_dict["author"] = user.name
        elif operation.entity_type == "workfile":
            if user and not payload_dict.get("created_by"):
                payload_dict["created_by"] = user.name
            if not payload_dict.get("updated_by"):
                payload_dict["updated_by"] = payload_dict["created_by"]
        entity = entity_class(project_name, payload_dict)
        if user:
            await entity.ensure_create_access(user)
        description = f"{operation.entity_type.capitalize()} {entity.name} created"
        events = [
            {
                "topic": f"entity.{operation.entity_type}.created",
                "summary": {"entityId": entity.id, "parentId": entity.parent_id},
                "description": description,
                "project": project_name,
                "user": user.name if user else None,
            }
        ]
        await entity.save(transaction=transaction)
        status = 201

    elif operation.type == "update":
        # in this case, thumbnailId is camelCase, since we pass a dict
        assert operation.data is not None, "data is required for update"
        thumbnail_only = len(operation.data) == 1 and "thumbnailId" in operation.data

        payload = entity_class.model.patch_model(**operation.data)
        assert operation.entity_id is not None, "entity_id is required for update"

        if operation.entity_type == "workfile":
            if not payload.updated_by:  # type: ignore
                payload.updated_by = user.name  # type: ignore

        entity = await entity_class.load(
            project_name,
            operation.entity_id,
            for_update=True,
            transaction=transaction,
        )
        if user:
            await entity.ensure_update_access(user, thumbnail_only=thumbnail_only)
        events = build_pl_entity_change_events(entity, payload)
        if user:
            for event in events:
                event["user"] = user.name
        entity.patch(payload)
        await entity.save(transaction=transaction)
        status = 204

    elif operation.type == "delete":
        assert operation.entity_id is not None, "entity_id is required for delete"
        entity = await entity_class.load(project_name, operation.entity_id)
        if user:
            await entity.ensure_delete_access(user)
        description = f"{operation.entity_type.capitalize()} {entity.name} deleted"

        if operation.force and user and not user.is_manager:
            raise ForbiddenException("Only managers can force delete")

        events = [
            {
                "topic": f"entity.{operation.entity_type}.deleted",
                "summary": {"entityId": entity.id, "parentId": entity.parent_id},
                "description": description,
                "project": project_name,
                "user": user.name if user else None,
            }
        ]
        if ayonconfig.audit_trail:
            events[0]["payload"] = {"entityData": entity.dict_simple()}
        await entity.delete(transaction=transaction, force=operation.force)
        status = 204

    else:
        # This should never happen (already validated)
        raise BadRequestException(f"Unknown operation type {operation.type}")

    return (
        entity,
        events,
        OperationResponseModel(
            id=operation.id,
            type=operation.type,
            entity_id=entity.id,
            entity_type=operation.entity_type,
            success=True,
            status=status,
        ),
    )


async def _process_operations(
    project_name: str,
    operations: list[OperationModel],
    user_map: dict[str, UserEntity | None],
    *,
    can_fail: bool = False,
    raise_on_error: bool = True,
    transaction: Connection | None = None,
) -> tuple[list[dict[str, Any]], OperationsResponseModel]:
    """Process a list of operations.

    This function should not raise an exception. If an operation
    fails, success=False is returned.

    Returns a tuple of:
     - list of events to dispatch
     - list of operation responses

    """

    result: list[OperationResponseModel] = []
    to_commit: list[ProjectLevelEntity] = []
    events: list[dict[str, Any]] = []

    for operation in operations:
        if operation.as_user:
            user = user_map.get(operation.as_user)
        else:
            user = None

        try:
            entity, evt, response = await _process_operation(
                project_name,
                user,
                operation,
                transaction=transaction,
            )
            if evt is not None:
                events.extend(evt)
            result.append(response)
            if entity.entity_type not in [e.entity_type for e in to_commit]:
                to_commit.append(entity)
        except AyonException as e:
            result.append(
                OperationResponseModel(
                    success=False,
                    id=operation.id,
                    type=operation.type,
                    status=e.status,
                    detail=e.detail,
                    entity_id=operation.entity_id,
                    entity_type=operation.entity_type,
                )
            )
            if not can_fail:
                if raise_on_error:
                    raise e
                break
        except IntegrityConstraintViolationError as e:
            parsed = parse_postgres_exception(e)

            result.append(
                OperationResponseModel(
                    success=False,
                    id=operation.id,
                    type=operation.type,
                    status=parsed["code"],
                    detail=parsed["detail"],
                    entity_id=operation.entity_id,
                    entity_type=operation.entity_type,
                )
            )

            if not can_fail:
                if raise_on_error:
                    raise ConflictException(parsed["detail"])
                break

        except Exception as e:
            result.append(
                OperationResponseModel(
                    success=False,
                    id=operation.id,
                    type=operation.type,
                    status=500,
                    detail=str(e),
                    entity_id=operation.entity_id,
                    entity_type=operation.entity_type,
                )
            )

            if not can_fail:
                if raise_on_error:
                    raise e
                break

    # Create overall success value
    success = all(op.success for op in result)
    if success or can_fail:
        for entity in to_commit:
            await entity.commit(transaction=transaction)

    return events, OperationsResponseModel(operations=result, success=success)


class ProjectLevelOperations:
    def __init__(
        self,
        project_name: str,
        *,
        user: UserEntity | None = None,
        sender: str | None = None,
        sender_type: str | None = None,
    ) -> None:
        self.user = user
        self.sender = sender
        self.sender_type = sender_type
        self.project_name = project_name
        self.operations: list[OperationModel] = []
        self.user_entities_map: dict[str, UserEntity | None] = {}

    def append(self, operation: OperationModel) -> None:
        """Append an operation to the list.

        This method is used internally or if you
        already have an OperationModel instance.
        """

        if operation.as_user:
            uname = operation.as_user
            if uname not in self.user_entities_map:
                self.user_entities_map[uname] = None
        elif self.user:
            uname = self.user.name
        else:
            uname = None

        assert isinstance(operation, OperationModel)
        operation.as_user = uname
        self.operations.append(operation)

    def add(
        self,
        operation_type: OperationType,
        entity_type: ProjectLevelEntityType,
        *,
        entity_id: str | None = None,
        as_user: str | UserEntity | None = None,
        operation_id: str | None = None,
        force: bool = False,
        **kwargs,
    ) -> None:
        if isinstance(as_user, UserEntity):
            self.user_entities_map[as_user.name] = as_user
            uname = as_user.name
        elif as_user:
            uname = as_user
        else:
            uname = None

        self.append(
            OperationModel(
                id=operation_id or create_uuid(),
                type=operation_type,
                entity_type=entity_type,
                entity_id=entity_id,
                as_user=uname,
                force=force,
                data=kwargs,
            )
        )

    def create(
        self,
        entity_type: ProjectLevelEntityType,
        entity_id: str | None = None,
        *,
        as_user: str | UserEntity | None = None,
        **kwargs,
    ) -> None:
        """Create a project level entity."""
        self.add(
            "create",
            entity_type,
            entity_id=entity_id,
            as_user=as_user,
            **kwargs,
        )

    def update(
        self,
        entity_type: ProjectLevelEntityType,
        entity_id: str,
        *,
        as_user: str | UserEntity | None = None,
        **kwargs,
    ) -> None:
        """Update a project level entity."""
        self.add(
            "update",
            entity_type,
            entity_id=entity_id,
            as_user=as_user,
            **kwargs,
        )

    def delete(
        self,
        entity_type: ProjectLevelEntityType,
        entity_id: str,
        *,
        as_user: str | UserEntity | None = None,
        force: bool = False,
    ) -> None:
        """Delete a project level entity."""
        self.add(
            "delete",
            entity_type,
            entity_id=entity_id,
            as_user=as_user,
            force=force,
        )

    # Cross-validation

    def _validate(self) -> None:
        """Run sanity checks on the operations list."""

        affected_entities: list[tuple[ProjectLevelEntityType, str]] = []
        for operation in self.operations:
            if operation.type == "create":
                # create should be safe.
                # It will fail if the is provided and is already exists,
                # but it will fail gracefully. No need to check for duplicates.
                continue

            if not operation.entity_id:
                raise BadRequestException("entity_id is required for update/delete")

            key = (operation.entity_type, operation.entity_id)
            if key in affected_entities:
                raise BadRequestException(
                    "Duplicate operation for "
                    f"{operation.entity_type} {operation.entity_id}"
                )
            affected_entities.append(key)

    # Processing

    async def _process(
        self,
        can_fail: bool = False,
        raise_on_error: bool = True,
    ) -> OperationsResponseModel:
        self._validate()

        events: list[dict[str, Any]] = []
        response: OperationsResponseModel = OperationsResponseModel(
            operations=[], success=False
        )  # keep the type checker happy

        # Load user entities that we will use for operaratins to
        # check access permissions and to dispatch events
        if self.user:
            self.user_entities_map[self.user.name] = self.user
        for uname in self.user_entities_map:
            if not self.user_entities_map[uname]:
                self.user_entities_map[uname] = await UserEntity.load(uname)

        if can_fail:
            events, response = await _process_operations(
                self.project_name,
                self.operations,
                user_map=self.user_entities_map,
                can_fail=True,
                raise_on_error=False,
            )

        else:
            with suppress(RollbackException):
                async with Postgres.acquire() as conn, conn.transaction():
                    events, response = await _process_operations(
                        self.project_name,
                        self.operations,
                        user_map=self.user_entities_map,
                        transaction=conn,
                        raise_on_error=raise_on_error,
                    )

                    if not response.success:
                        events = []
                        # Raise rollback exception to roll back the transaction
                        # but silence it so the response is returned
                        raise RollbackException()

        for event in events:
            await EventStream.dispatch(
                sender=self.sender,
                sender_type=self.sender_type,
                **event,
            )

        return response

    async def process(
        self,
        can_fail: bool = False,
        raise_on_error: bool = True,
    ) -> OperationsResponseModel:
        """
        Process the enqueued operations.

        raise_on_error is ignored if can_fail is True, when set to False,
        the function will return the response even if there are errors (success=False).
        When raise_on_error is True, the function will raise an exception when the first
        error is encountered, and the response will not be returned.

        Warning: Method can still raise BadRequestException even if can_fail is True or
        raise_on_error is False, if the request is invalid
        (e.g. missing entity_id for update)
        """

        try:
            return await self._process(can_fail=can_fail, raise_on_error=raise_on_error)
        finally:
            self.operations = []
