from typing import Any

from pydantic import validator

from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.settings import BaseSettingsModel, SettingsField


class CustomizationModel(BaseSettingsModel):
    login_background: str = SettingsField(
        "",
        title="Login Background",
        disabled=True,
    )
    studio_logo: str = SettingsField(
        "",
        title="Studio Logo",
        disabled=True,
    )

    motd: str = SettingsField(
        "",
        title="Message of the Day",
        description="The message of the day that "
        "is displayed to users on the login page. "
        "Markdown syntax is supported.",
        example="Welcome to Ayon!",
        widget="textarea",
    )

    @validator("login_background", "studio_logo", pre=True)
    def validate_image_url(cls, value: str) -> str:
        if not value:
            return ""
        return value


class ServerConfigModel(BaseSettingsModel):
    _layout: str = "root"

    studio_name: str = SettingsField(
        "",
        description="The name of the studio",
        example="Ynput",
    )

    customization: CustomizationModel = SettingsField(
        CustomizationModel(),
        title="Customization",
        description="Customization options for the login page",
    )


def migrate_server_config(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Migrate the server configuration from the old format to the new format."""
    return config_dict


async def build_server_config_cache() -> dict[str, Any]:
    q = "SELECT value FROM config WHERE key = 'serverConfig'"
    res = await Postgres.fetchrow(q)
    if not res:
        data = {}
    else:
        data = res["value"]
    data = migrate_server_config(data)
    await Redis.set_json("server", "config", data)
    return data


async def get_server_config_overrides() -> dict[str, Any]:
    data = await Redis.get_json("server", "config")
    if data is None:
        return await build_server_config_cache()
    return data


async def get_server_config() -> ServerConfigModel:
    """Return the server configuration."""
    data = await Redis.get_json("server", "config")
    if data is None:
        data = await build_server_config_cache()
    return ServerConfigModel(**data)


async def save_server_config_data(data: dict[str, Any]) -> None:
    await Postgres.execute(
        """
        INSERT INTO config (key, value)
        VALUES ('serverConfig', $1)
        ON CONFLICT (key) DO UPDATE SET value = $1
        """,
        data,
    )
    await build_server_config_cache()
