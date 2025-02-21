from typing import Any

from ayon_server.auth.utils import create_password, hash_password
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger


async def deploy_users(
    users: list[dict[str, Any]],
    projects: list[str],
) -> None:
    """Create users in the database."""
    if not users:
        return

    for user in users:
        name = user["name"]
        attrib = {}
        data: dict[str, Any] = {}

        for key in ("fullName", "email"):
            if key in user:
                attrib[key] = user[key]

        for key in ("isManager", "isAdmin", "isService", "isGuest"):
            if key in user:
                data[key] = user[key]

        if "password" in user:
            logger.debug(f"Creating password for user {name}")
            data["password"] = create_password(user["password"])

        if "hashedPassword" in user:
            logger.debug(f"Adding password for user {name}")
            data["password"] = user["hashedPassword"]

        if "apiKey" in user:
            api_key = user["apiKey"]
            logger.debug(f"Creating api key for user {name}")
            api_key_preview = api_key[:4] + "***" + api_key[-4:]
            data["apiKey"] = hash_password(api_key)
            data["apiKeyPreview"] = api_key_preview

        data["defaultAccessGroups"] = user.get("defaultAccessGroups", [])
        assert isinstance(data["defaultAccessGroups"], list)
        assert all(isinstance(role, str) for role in data["defaultAccessGroups"])

        data["accessGroups"] = {
            project_name: data["defaultAccessGroups"]
            for project_name in projects
            if data["defaultAccessGroups"]
            and isinstance(data["defaultAccessGroups"], list)
        }

        for project_name, access_groups in user.get("accessGroups", {}).items():
            if access_groups and isinstance(access_groups, list):
                data["accessGroups"][project_name] = access_groups
            elif data["accessGroups"].get(project_name):
                del data["accessGroups"][project_name]

        res = await Postgres.fetch("SELECT * FROM users WHERE name = $1", name)
        if res and not user.get("forceUpdate"):
            logger.info(f"{user['name']} already exists. skipping")
            continue

        logger.info(f"Saving user {user['name']}")
        await Postgres.execute(
            """
            INSERT INTO public.users (name, active, attrib, data)
            VALUES ($1, TRUE,  $2, $3)
            ON CONFLICT (name) DO UPDATE
            SET active = TRUE, attrib = $2, data = $3
            """,
            name,
            attrib,
            data,
        )

    # Migration from 0.3.0 to 0.4.0
    # TODO: remove in 0.5.0

    async for row in Postgres.iterate("SELECT name, data FROM users"):
        name = row["name"]
        data = row["data"]
        need_update = False

        dr = data.pop("defaultRoles", None)
        if dr:
            need_update = True
            data["defaultAccessGroups"] = dr

        r = data.pop("roles", None)
        if r:
            need_update = True
            data["accessGroups"] = r

        if need_update:
            await Postgres.execute(
                "UPDATE users SET data = $1 WHERE name = $2", data, name
            )
