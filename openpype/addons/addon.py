import os

try:
    import toml
except ModuleNotFoundError:
    toml = None

from typing import TYPE_CHECKING, Any, Callable, Type

from openpype.exceptions import NotFoundException
from openpype.lib.postgres import Postgres
from openpype.settings import BaseSettingsModel, apply_overrides

if TYPE_CHECKING:
    from openpype.addons.definition import ServerAddonDefinition


class BaseServerAddon:
    name: str
    title: str | None = None
    version: str
    addon_type: str = "module"
    definition: "ServerAddonDefinition"
    endpoints: list[dict[str, Any]]
    settings_model: Type[BaseSettingsModel] | None = None
    frontend_scopes: dict[str, Any] = {}

    def __init__(self, definition: "ServerAddonDefinition", addon_dir: str):
        assert self.name and self.version
        self.definition = definition
        self.addon_dir = addon_dir
        self.endpoints = []

        # Ensure name was not changed during versions, update definition.name and title
        # TODO: maybe move this to the definition
        if definition.versions:
            if self.name != definition.name:
                raise ValueError(f"name mismatch {self.name} != {definition.name}")
            if self.addon_type != definition.addon_type:
                raise ValueError(
                    f"type mismatch {self.addon_type} != {definition.addon_type}"
                )
        else:
            definition.name = self.name
            definition.addon_type = self.addon_type

        if self.title:
            definition.title = self.title

        self.setup()

    def __repr__(self) -> str:
        return f"<Addon name='{self.definition.name}' version='{self.version}'>"

    @property
    def friendly_name(self) -> str:
        """Return the friendly name of the addon."""
        return f"{self.definition.friendly_name} {self.version}"

    #
    # File serving
    #

    def get_frontend_dir(self) -> str | None:
        """Return the addon frontend directory."""
        res = os.path.join(self.addon_dir, "frontend/dist")
        if os.path.isdir(res):
            return res

    def get_resources_dir(self) -> str | None:
        """Return the addon resources directory.

        This directory contains the client code, pyproject.toml,
        icons and additional resources. If the directory exists,
        it is served via http on:
            /resources/{addon_name}/{addon/version}/{path}
        """
        res = os.path.join(self.addon_dir, "resources")
        if os.path.isdir(res):
            return res

    async def get_client_pyproject(self) -> dict[str, Any] | None:
        if self.get_resources_dir() is None:
            return None
        pyproject_path = os.path.join(self.get_resources_dir(), "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return None
        if toml is None:
            return {"error": "Toml is not installed (but pyproject exists)"}
        return toml.load(open(pyproject_path))

    async def get_client_source_info(
        self,
        base_url: str | None = None,
    ) -> list[dict[str:Any]] | None:
        if self.get_resources_dir() is None:
            return None
        if base_url is None:
            base_url = ""
        local_path = os.path.join(self.get_resources_dir(), "client.zip")
        if not os.path.exists(local_path):
            return None
        return [
            {
                "type": "http",
                "path": f"{base_url}/resources/{self.name}/{self.version}/client.zip",
            }
        ]

    #
    # Settings
    #

    def get_settings_model(self) -> Type[BaseSettingsModel] | None:
        return self.settings_model

    async def get_studio_overrides(self) -> dict[str, Any]:
        """Load the studio overrides from the database."""

        res = await Postgres.fetch(
            f"""
            SELECT data FROM settings
            WHERE addon_name = '{self.definition.name}'
            AND addon_version = '{self.version}'
            ORDER BY snapshot_time DESC LIMIT 1
            """
        )
        if res:
            return dict(res[0]["data"])
        return {}

    async def get_project_overrides(self, project_name: str) -> dict[str, Any]:
        """Load the project overrides from the database."""

        try:
            res = await Postgres.fetch(
                f"""
                SELECT data FROM project_{project_name}.settings
                WHERE addon_name = '{self.definition.name}'
                AND addon_version = '{self.version}'
                ORDER BY snapshot_time DESC LIMIT 1
                """
            )
        except Postgres.UndefinedTableError:
            raise NotFoundException(f"Project {project_name} does not exists")
        if res:
            return dict(res[0]["data"])
        return {}

    async def get_studio_settings(self) -> BaseSettingsModel | None:
        """Return the addon settings with the studio overrides.

        You shouldn't override this method, unless absolutely necessary.
        """

        settings = await self.get_default_settings()
        if settings is None:
            return None  # this addon has no settings at all
        overrides = await self.get_studio_overrides()
        if overrides:
            settings = apply_overrides(settings, overrides)

        return settings

    async def get_project_settings(self, project_name: str) -> BaseSettingsModel | None:
        """Return the addon settings with the studio and project overrides.

        You shouldn't override this method, unless absolutely necessary.
        """

        settings = await self.get_studio_settings()
        if settings is None:
            return None  # this addon has no settings at all
        studio_overrides = await self.get_studio_overrides()
        if studio_overrides:
            settings = apply_overrides(settings, studio_overrides)
        project_overrides = await self.get_project_overrides(project_name)
        if project_overrides:
            settings = apply_overrides(settings, project_overrides)
        return settings

    #
    # Overridable methods
    #

    async def get_default_settings(self) -> BaseSettingsModel | None:
        """Get the default addon settings.

        Override this method to return the default settings for the addon.
        By default it returns defaults from the addon's settings model, but
        if you need to use a complex model or force required fields, you should
        do something like: `return self.get_settings_model(**YOUR_ADDON_DEFAULTS)`.
        """

        if (model := self.get_settings_model()) is None:
            return None
        return model()

    def convert_system_overrides(
        self,
        source_version: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert system overrides from a previous version."""
        return overrides

    def convert_project_overrides(
        self,
        from_version: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert project overrides from a previous version."""
        return overrides

    def setup(self) -> None:
        """Setup the addon."""
        pass

    def add_endpoint(
        self,
        path: str,
        handler: Callable,
        *,
        method: str = "GET",
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Add a REST endpoint to the server."""

        self.endpoints.append(
            {
                "name": name or handler.__name__,
                "path": path,
                "handler": handler,
                "method": method,
                "description": description or handler.__doc__ or "",
            }
        )
