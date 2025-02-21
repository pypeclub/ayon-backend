"""User entity."""

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Optional

from ayon_server.access.access_groups import AccessGroups
from ayon_server.access.permissions import Permissions
from ayon_server.auth.utils import (
    create_password,
    ensure_password_complexity,
    hash_password,
)
from ayon_server.constraints import Constraints
from ayon_server.entities.core import TopLevelEntity, attribute_library
from ayon_server.entities.models import ModelSet
from ayon_server.exceptions import (
    ConstraintViolationException,
    ForbiddenException,
    LowPasswordComplexityException,
    NotFoundException,
)
from ayon_server.helpers.email import send_mail
from ayon_server.helpers.project_list import get_project_list
from ayon_server.lib.postgres import Connection, Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.types import AccessType
from ayon_server.utils import SQLTool, dict_exclude

if TYPE_CHECKING:
    from ayon_server.api.clientinfo import ClientInfo
    from ayon_server.auth.session import SessionModel


class SessionInfo:
    is_api_key: bool = False
    client_info: Optional["ClientInfo"] = None

    def __init__(self, session: "SessionModel") -> None:
        self.is_api_key = session.is_api_key
        self.client_info = session.client_info

    def __repr__(self) -> str:
        return f"SessionInfo(is_api_key={self.is_api_key})"


class UserEntity(TopLevelEntity):
    entity_type: str = "user"
    model = ModelSet("user", attribute_library["user"], has_id=False)
    was_active: bool = False
    original_email: str | None = None
    session: SessionInfo | None = None

    # Cache for path access lists
    # the structure is as follows:
    # project_name[access_type]: [path1, path2, ...]
    path_access_cache: dict[str, dict[AccessType, list[str]]] | None = None
    save_hooks: list[Callable[["UserEntity", Connection], Awaitable[None]]] = []

    #
    # Load
    #

    def __init__(
        self,
        payload: dict[str, Any],
        exists: bool = False,
        validate: bool = True,  # deprecated
    ) -> None:
        super().__init__(payload, exists, validate)
        self.was_active = self.active and self.exists
        self.was_service = self.is_service and self.exists
        self.original_email = self.attrib.email

    @classmethod
    async def load(
        cls,
        name: str,
        transaction: Connection | None = None,
        for_update: bool = False,
    ) -> "UserEntity":
        """Load a user from the database."""

        if not (
            user_data := await Postgres.fetch(
                f"""
                SELECT * FROM public.users WHERE name = $1
                {'FOR UPDATE' if transaction and for_update else ''}
                """,
                name,
            )
        ):
            raise NotFoundException(f"Unable to load user {name}")
        return cls.from_record(user_data[0])

    def add_session(self, session: "SessionModel") -> None:
        self.session = SessionInfo(session)

    #
    # Save
    #

    async def save(
        self,
        transaction: Connection | None = None,
        run_hooks: bool = True,
    ) -> bool:
        """Save the user to the database."""

        conn = transaction or Postgres

        if self.is_service:
            do_con_check = False
            self.data.pop("password", None)  # Service accounts can't have passwords
            self.attrib.email = None  # Nor emails

        elif self.active and not self.was_active:
            # activating previously inactive user
            do_con_check = True

        elif not self.is_service and self.was_service:
            # turning service account into regular user
            # this is still possible via API, but not via UI
            # so we need to check constraints
            do_con_check = True

        else:
            do_con_check = False

        if not self.active:
            self.data.pop("userPool", None)

        if self.attrib.email and (self.attrib.email != self.original_email):
            logger.info(f"Email changed for user {self.name}")
            # Email changed, we need to check if it's unique
            # We cannot use DB index here.
            res = await conn.fetch(
                """
                SELECT name FROM users
                WHERE LOWER(attrib->>'email') = $1
                AND name != $2
                """,
                self.attrib.email.lower(),
                self.name,
            )

            if res:
                msg = "This email is already used by another user"
                raise ConstraintViolationException(msg)

        if do_con_check:
            logger.info(f"Activating user {self.name}")

            if (max_users := await Constraints.check("maxActiveUsers")) is not None:
                max_users = max_users or 1
                res = await conn.fetch(
                    """
                    SELECT count(*) as cnt FROM users
                    WHERE active is TRUE
                    AND coalesce(data->>'isService', 'false') != 'true'
                    """
                )
                if res and res[0]["cnt"] >= max_users:
                    raise ForbiddenException(
                        f"Maximum number of users ({max_users}) reached"
                    )

        if self.exists:
            data = dict_exclude(
                self.dict(exclude_none=True), ["ctime", "name", "own_attrib"]
            )
            await conn.execute(
                *SQLTool.update(
                    "public.users",
                    f"WHERE name='{self.name}'",
                    **data,
                )
            )
        else:
            await conn.execute(
                *SQLTool.insert(
                    "users",
                    **dict_exclude(self.dict(exclude_none=True), ["own_attrib"]),
                )
            )
            await Redis.delete("user.avatar", self.name)
            self.exists = True

        if run_hooks:
            for hook in self.save_hooks:
                await hook(self, conn)  # type: ignore
        return True

    #
    # Delete
    #

    async def delete(
        self,
        transaction: Connection | None = None,
    ) -> bool:
        """Delete existing user."""
        if not self.name:
            raise NotFoundException(f"Unable to delete user {self.name}. Not loaded.")

        async def post_delete(conn) -> int:
            res = await conn.fetch(
                """
                WITH deleted AS (
                    DELETE FROM users
                    WHERE name=$1
                    RETURNING *
                ) SELECT count(*) FROM deleted;
                """,
                self.name,
            )

            # Unassign user from all tasks
            projects = await get_project_list()
            for project in projects:
                query = f"""
                    UPDATE project_{project.name}.tasks Set
                    assignees = array_remove(assignees, '{self.name}')
                    WHERE '{self.name}' = any(assignees)
                """
                await conn.execute(query)

            return res[0]["count"]

        if transaction:
            deleted = await post_delete(transaction)
        else:
            async with Postgres.acquire() as conn, conn.transaction():
                deleted = await post_delete(conn)
                await self.commit(conn)

        return bool(deleted)

    #
    # Authorization helpers
    #

    @property
    def is_service(self) -> bool:
        """
        Service accounts have similar rights as administrators,
        but they also can act as a different user (sudo-style)
        """
        return self.data.get("isService", False)

    @property
    def is_admin(self) -> bool:
        if self.is_guest:
            return False
        return self.data.get("isAdmin", False) or self.is_service

    @property
    def is_guest(self) -> bool:
        return self.data.get("isGuest", False)

    @property
    def is_developer(self) -> bool:
        return self.data.get("isDeveloper", False)

    @property
    def is_manager(self) -> bool:
        data = self.data
        return (
            data.get("isManager", False)
            or data.get("isAdmin", False)
            or data.get("isService", False)
        )

    def check_permissions(
        self, key: str, project_name: str | None = None, **kwargs: Any
    ) -> None:
        """
        Check if user has a specific permission.

        Raise forbidden exception if user does not have the permission.

        common kwargs:
        - addon: str

        """
        if self.is_manager:
            return

        permissions = self.permissions(project_name)

        try:
            group, perm_name = key.split(".")
        except ValueError:
            raise ValueError(f"Invalid permission key {key}")

        perm_group = getattr(permissions, group)
        if group not in ["studio", "project"] and not perm_group.enabled:
            # no restrictions on group (folder access)
            return

        try:
            perm = getattr(perm_group, perm_name)
        except AttributeError:
            raise ForbiddenException(f"Your permissions do not include {key}")
        if not perm:
            pdef = f" {project_name}" if project_name else ""
            raise ForbiddenException(f"You are not allowed to access{pdef} {perm_name}")

        if kwargs.get("write") and int(perm) < 2:
            pdef = f" {project_name}" if project_name else ""
            raise ForbiddenException(f"You are not allowed to modify{pdef} {perm_name}")

    def check_project_access(self, project_name: str) -> None:
        if self.is_manager:
            return
        access_groups = [k.lower() for k in self.data.get("accessGroups", {})]
        if project_name.lower() not in access_groups:
            raise ForbiddenException("No access group assigned on this project")

    def permissions(self, project_name: str | None = None) -> Permissions:
        """Return user permissions on a given project."""

        if project_name is None:
            active_access_groups = self.data.get("defaultAccessGroups", [])

        else:
            try:
                access_groups = {
                    k.lower(): v for k, v in self.data.get("accessGroups", {}).items()
                }
                active_access_groups = access_groups[project_name.lower()]
            except KeyError:
                raise ForbiddenException("No access group assigned on this project")

        return AccessGroups.combine(active_access_groups, project_name or "_")

    def set_password(
        self,
        password: str | None,
        complexity_check: bool = False,
    ) -> None:
        """Set user password."""

        if password is None:
            self.data.pop("password", None)
            return

        if complexity_check and not ensure_password_complexity(password):
            raise LowPasswordComplexityException
        hashed_password = create_password(password)
        self.data["password"] = hashed_password

    def set_api_key(self, api_key: str | None) -> None:
        """Set user api key."""

        if api_key is None:
            self.data.pop("apiKey", None)
            self.data.pop("apiKeyPreview", None)
            return

        assert re.match(r"^[a-zA-Z0-9]{32}$", api_key)
        api_key_preview = api_key[:4] + "***" + api_key[-4:]

        self.data["apiKey"] = hash_password(api_key)
        self.data["apiKeyPreview"] = api_key_preview

    async def send_mail(
        self,
        subject: str,
        text: str | None = None,
        html: str | None = None,
    ) -> None:
        """Send email to user."""

        recipient = self.attrib.email
        if not recipient:
            raise ValueError(f"User {self.name} has no email address")

        if self.attrib.fullName:
            recipient = f"{self.attrib.fullName} <{recipient}>"

        await send_mail([recipient], subject, text, html)
