import time

from ayon_server.auth.models import LoginResponseModel
from ayon_server.auth.session import Session
from ayon_server.entities import UserEntity
from ayon_server.exceptions import ForbiddenException
from ayon_server.lib.postgres import Postgres
from ayon_server.types import Field, OPModel
from ayon_server.utils import create_hash
from nxtools import logging

from .router import router

TOKEN_TTL = 3600
PASSWORD_RESET_EMAIL_TEMPLATE_PLAIN = """
Hello {name},
it seems that you have requested a password reset for your account for Ayon.
Please follow this link to reset your password:

{reset_url}?token={token}
"""

PASSWORD_RESET_EMAIL_TEMPLATE_HTML = """
<p>Hello, {name}</p>

<p>it seems that you have requested a password reset for your account for Ayon.
Please follow this link to reset your password:</p>

<p><a href="{reset_url}?token={token}">Reset password</a></p>
"""


class PasswordResetRequestModel(OPModel):
    email: str = Field(..., title="Email", example="you@somewhere.com")
    url: str = Field(...)


@router.post("/passwordResetRequest")
async def password_reset_request(request: PasswordResetRequestModel):
    pass

    async for row in Postgres.iterate(
        "SELECT name, data FROM users WHERE LOWER(attrib->>'email') = $1",
        request.email.lower(),
    ):
        user_data = row["data"]
        break
    else:
        logging.error(
            f"Attempted password reset using non-existent email: {request.email}"
        )
        return

    password_reset_request = user_data.get("passwordResetRequest", {})
    password_requet_time = password_reset_request.get("time", None)

    if password_requet_time and (time.time() - password_requet_time) < TOKEN_TTL:
        raise ForbiddenException(
            "Attempted password reset too soon after previous attempt"
        )

    token = create_hash()
    password_reset_request = {
        "time": time.time(),
        "token": token,
    }

    user = await UserEntity.load(row["name"])
    user.data["passwordResetRequest"] = password_reset_request

    tplvars = {
        "token": token,
        "reset_url": request.url,
        "name": user.attrib.fullName or user.name,
    }

    await user.save()
    await user.send_mail(
        "Ayon password reset",
        text=PASSWORD_RESET_EMAIL_TEMPLATE_PLAIN.format(**tplvars),
        html=PASSWORD_RESET_EMAIL_TEMPLATE_HTML.format(**tplvars),
    )
    logging.info(f"Sent password reset email to {request.email}")


class PasswordResetModel(OPModel):
    token: str = Field(..., title="Token")
    password: str | None = Field(None, title="New password")


@router.post("/passwordReset")
async def password_reset(request: PasswordResetModel) -> LoginResponseModel:
    query = (
        "SELECT name, data FROM users WHERE data->'passwordResetRequest'->>'token' = $1"
    )

    ERROR_MESSAGE = "Invalid reset token or token has expired"

    async for row in Postgres.iterate(query, request.token):
        user_name = row["name"]
        user_data = row["data"]
        break
    else:
        logging.error("Attempted password reset using invalid token")
        raise ForbiddenException("Invalid token")

    password_reset_request = user_data.get("passwordResetRequest", {})
    password_request_time = password_reset_request.get("time", None)

    if not password_request_time or (time.time() - password_request_time) > TOKEN_TTL:
        logging.error("Attempted password reset using expired token")
        raise ForbiddenException(ERROR_MESSAGE)

    if request.password is None:
        # just checking whether the token is valid
        # we don't set the password
        return LoginResponseModel(detail="Token is valid")

    user = await UserEntity.load(user_name)
    user.data["passwordResetRequest"] = None
    user.set_password(request.password, complexity_check=True)
    await user.save()

    session = await Session.create(user)
    return LoginResponseModel(
        detail="Password changed",
        token=session.token,
        user=session.user,
    )
