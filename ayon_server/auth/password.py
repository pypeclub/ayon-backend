import time

from fastapi import Request

from ayon_server.api.clientinfo import get_real_ip
from ayon_server.auth.session import Session, SessionModel
from ayon_server.auth.utils import (
    create_password,
    ensure_password_complexity,
    hash_password,
)
from ayon_server.config import ayonconfig
from ayon_server.entities import UserEntity
from ayon_server.events import EventStream
from ayon_server.exceptions import ForbiddenException
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger
from ayon_server.utils import json_dumps


async def check_failed_login(ip_address: str) -> None:
    banned_until = await Redis.get("banned-ip-until", ip_address)
    if banned_until is None:
        return

    if float(banned_until) > time.time():
        msg = (
            f"Attempt to login from banned IP {ip_address}. "
            f"Retry in {float(banned_until) - time.time():.2f} seconds."
        )
        await EventStream.dispatch(
            "user.log_fail",
            description=msg,
            summary={"ip": ip_address},
        )
        await Redis.delete("login-failed-ip", ip_address)
        raise ForbiddenException("Too many failed login attempts")


async def set_failed_login(ip_address: str):
    ns = "login-failed-ip"
    failed_attempts = await Redis.incr(ns, ip_address)
    await Redis.expire(
        ns, ip_address, 600
    )  # this is just for the clean-up, it cannot be used to reset the counter

    if failed_attempts > ayonconfig.max_failed_login_attempts:
        await Redis.set(
            "banned-ip-until",
            ip_address,
            json_dumps(time.time() + ayonconfig.failed_login_ban_time),
        )


async def clear_failed_login(ip_address: str):
    await Redis.delete("login-failed-ip", ip_address)


class PasswordAuth:
    @classmethod
    async def login(
        cls,
        name: str,
        password: str,
        request: Request | None = None,
    ) -> SessionModel:
        """Login using username/password credentials.

        Return a SessionModel object if the credentials are valid.
        Raise 403 if the credentials are invalid.
        """
        # TODO: this should raise 401, not 403

        if request is not None:
            await check_failed_login(get_real_ip(request))

        name = name.strip()

        # name active attrib data

        result = await Postgres.fetch(
            "SELECT * FROM public.users WHERE name ilike $1", name
        )
        if not result:
            raise ForbiddenException("Invalid login/password combination")

        user = UserEntity.from_record(result[0])

        if user.is_service:
            raise ForbiddenException("Service users cannot log in")

        if "password" not in user.data:
            raise ForbiddenException("Password login is not enabled for this user")

        pass_hash, pass_salt = user.data["password"].split(":")

        if pass_hash != hash_password(password, pass_salt):
            if request is not None:
                await set_failed_login(get_real_ip(request))
            raise ForbiddenException("Invalid login/password combination")

        if request is not None:
            await clear_failed_login(get_real_ip(request))
        return await Session.create(user, request)

    @classmethod
    async def change_password(cls, name: str, password: str) -> None:
        """Change password for a user."""
        if not ensure_password_complexity(password):
            raise ValueError("Password does not meet complexity requirements")

        result = await Postgres.fetch(
            "SELECT data FROM public.users WHERE name = $1", name
        )
        if not result:
            logger.error(f"Unable to change password. User {name} not found")
            return

        user_data = result[0][0] or {}
        user_data["password"] = create_password(password)

        await Postgres.execute(
            "UPDATE public.users SET data = $1 WHERE name = $2",
            user_data,
            name,
        )
