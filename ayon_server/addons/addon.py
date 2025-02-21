import os

try:
    import toml
except ModuleNotFoundError:
    toml = None  # type: ignore

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from ayon_server.actions.context import ActionContext
from ayon_server.actions.execute import ActionExecutor, ExecuteResponseModel
from ayon_server.actions.manifest import (
    DynamicActionManifest,
    SimpleActionManifest,
)
from ayon_server.addons.models import ServerSourceInfo, SourceInfo, SSOOption
from ayon_server.exceptions import AyonException, BadRequestException, NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.settings import BaseSettingsModel, apply_overrides
from ayon_server.settings.common import migrate_settings_overrides
from nxtools import log_traceback, logging

if TYPE_CHECKING:
    from fastapi import APIRouter

    from ayon_server.addons.definition import ServerAddonDefinition

METADATA_KEYS = [
    "name",
    "version",
    "title",
    "services",
    # compatibility object
    "ayon_server_version",
    "ayon_launcher_version",
    "ayon_required_addons",
    "ayon_soft_required_addons",
    "ayon_compatible_addons",
]


class AddonCompatibilityModel(BaseSettingsModel):
    server_version: str | None = None
    launcher_version: str | None = None
    required_addons: dict[str, str | None] | None = None
    soft_required_addons: dict[str, str | None] | None = None
    compatible_addons: dict[str, str | None] | None = None


class BaseServerAddon:
    # metadata from package.py
    name: str
    version: str
    title: str | None = None
    services: dict[str, Any] = {}

    # should be defined on addon class
    addon_type: Literal["server", "pipeline"] = "pipeline"
    system: bool = False  # Hide settings for non-admins and make the addon mandatory
    settings_model: type[BaseSettingsModel] | None = None
    site_settings_model: type[BaseSettingsModel] | None = None
    app_host_name: str | None = None
    frontend_scopes: dict[str, Any] = {}
    frontend_modules: dict[str, Any] = {}

    compatibility: AddonCompatibilityModel | None = None

    # automatically set
    definition: "ServerAddonDefinition"
    legacy: bool = False  # auto-set to true if it is the old style addon
    endpoints: list[dict[str, Any]]
    routers: list["APIRouter"]

    def __init__(self, definition: "ServerAddonDefinition", addon_dir: str, **kwargs):
        # populate metadata from package.py

        compatibility = AddonCompatibilityModel(
            server_version=kwargs.pop("ayon_server_version", None),
            launcher_version=kwargs.pop("ayon_launcher_version", None),
            required_addons=kwargs.pop("ayon_required_addons", None),
            soft_required_addons=kwargs.pop("ayon_soft_required_addons", None),
            compatible_addons=kwargs.pop("ayon_compatible_addons", None),
        )

        self.compatibility = compatibility

        for key in METADATA_KEYS:
            if key in kwargs:
                setattr(self, key, kwargs[key])

        # ensure name and version are set
        assert (
            self.name and self.version
        ), f"Addon {addon_dir} is missing name or version"

        self.definition = definition
        self.addon_dir = addon_dir
        self.endpoints = []
        self.routers = []
        self.restart_requested = False
        logging.debug(f"Initializing addon {self.name} v{self.version}")
        self.initialize()

    def __repr__(self) -> str:
        return f"<Addon name='{self.definition.name}' version='{self.version}'>"

    @property
    def friendly_name(self) -> str:
        """Return the friendly name of the addon."""
        return f"{self.definition.friendly_name} {self.version}"

    async def is_production(self) -> bool:
        """Return True if the addon is in production bundle."""
        res = await Postgres.fetch(
            "SELECT data FROM bundles WHERE is_production IS true"
        )
        if not res:
            return False
        production_addons = res[0]["data"].get("addons", {}) or {}
        if self.name not in production_addons:
            return False
        if production_addons[self.name] != self.version:
            return False
        return True

    def initialize(self) -> None:
        """Initialize the addon.

        This metod is started during the addon initialization
        and it is here just for the convinience (override this
        instead using super().__init__).

        Add `add_endpoint` calls here. This method cannot be async.
        """
        pass

    def pre_setup(self) -> None:
        """Pre-Setup the addon.

        This method is called when all addons are initialized.
        Add code which needs to access other addons here.

        This method may be async if needed (for example when)
        it needs to access the database.

        it is the same as setup, but allows two step setup.
        first pre_setup is called for all addons, then setup is called
        for all addons.
        """
        return None

    def setup(self) -> None:
        """Setup the addon.

        This method is called when all addons are initialized.
        Add code which needs to access other addons here.

        This method may be async if needed (for example when)
        it needs to access the database.
        """
        return None

    def request_server_restart(self):
        """Request the server to restart.

        call this method from initialize or setup to request server restart.
        For example when you change the server configuration. If called from
        initialize the server will restart after all addons are initialized.
        If called from setup the server will restart after all addons are
        setup.
        """
        logging.info(f"Addon {self.name}:{self.version} requested server restart")
        self.restart_requested = True

    def add_router(self, router: "APIRouter") -> None:
        self.routers.append(router)

    def add_endpoint(
        self,
        path: str,
        handler: Callable[..., Any],
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

    async def get_frontend_scopes(self) -> dict[str, Any]:
        return self.frontend_scopes

    async def get_frontend_modules(self) -> dict[str, Any]:
        return self.frontend_modules

    #
    # File serving
    #
    # Each addon supports serving files from the following directories:
    #  - frontend (html/javascript frontend code with index.html)
    #  - private (files which require authentication header to download)
    #  - public (files available for unauthenticated download)
    #

    def get_frontend_dir(self) -> str | None:
        """Return the addon frontend directory."""
        res = os.path.join(self.addon_dir, "frontend/dist")
        if os.path.isdir(res):
            return res
        return None  # just to make mypy happy

    def get_private_dir(self) -> str | None:
        """Return the addon private directory."""
        res = os.path.join(self.addon_dir, "private")
        if os.path.isdir(res):
            return res
        return None

    def get_public_dir(self) -> str | None:
        """Return the addon private directory."""
        res = os.path.join(self.addon_dir, "public")
        if os.path.isdir(res):
            return res
        return None

    #
    # Client code
    #

    def get_local_client_info(
        self,
        base_url: str | None = None,
    ) -> ServerSourceInfo | None:
        """Returns information on local copy of the client code."""
        if (pdir := self.get_private_dir()) is None:
            return None
        filename = "client.zip"
        local_path = os.path.join(pdir, filename)
        if not os.path.exists(local_path):
            return None
        return ServerSourceInfo(filename=filename)

    async def get_client_source_info(
        self,
        base_url: str | None = None,
    ) -> list[SourceInfo] | None:
        """Return a list of locations from where the client part of
        the addon can be downloaded.
        """

        if (local := self.get_local_client_info(base_url)) is None:
            return None
        return [local]

    async def get_client_pyproject(self) -> dict[str, Any] | None:
        if (pdir := self.get_private_dir()) is None:
            return None
        pyproject_path = os.path.join(pdir, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return None
        if toml is None:
            return {"error": "Toml is not installed (but pyproject exists)"}
        try:
            return toml.load(open(pyproject_path))
        except Exception:
            raise AyonException("Unable to parse pyproject.toml") from None

    #
    # Settings
    #

    def get_settings_model(self) -> type[BaseSettingsModel] | None:
        return self.settings_model

    def get_site_settings_model(self) -> type[BaseSettingsModel] | None:
        return self.site_settings_model

    # Load overrides from the database

    async def get_studio_overrides(
        self,
        variant: str = "production",
        as_version: str | None = None,
    ) -> dict[str, Any]:
        """Load the studio overrides from the database."""

        query = """
            SELECT data FROM settings
            WHERE addon_name = $1 AND addon_version = $2 AND variant = $3
            """

        res = await Postgres.fetch(query, self.definition.name, self.version, variant)
        data = {}
        if res:
            data = res[0]["data"]

        if as_version and as_version != self.version:
            target_addon = self.definition.get(as_version)
            if target_addon is None:
                raise BadRequestException(
                    f"Unable to parse {self} settings as {as_version}"
                    "Target addon not found"
                )

            try:
                return await target_addon.convert_settings_overrides(
                    self.version, overrides=data
                )
            except Exception:
                log_traceback(f"Unable to migrate {self} settings to {as_version}")
                return {}

        return data

    async def get_project_overrides(
        self,
        project_name: str,
        variant: str = "production",
        as_version: str | None = None,
    ) -> dict[str, Any]:
        """Load the project overrides from the database."""

        query = f"""
            SELECT data FROM project_{project_name}.settings
            WHERE addon_name = $1 AND addon_version = $2 AND variant = $3
            """

        try:
            res = await Postgres.fetch(
                query, self.definition.name, self.version, variant
            )
        except Postgres.UndefinedTableError:
            raise NotFoundException(f"Project {project_name} does not exists") from None
        data = {}
        if res:
            data = res[0]["data"]

        if as_version and as_version != self.version:
            target_addon = self.definition.get(as_version)
            if target_addon is None:
                raise BadRequestException(
                    f"Unable to parse {self} settings as {as_version}"
                    "Target addon not found"
                )

            try:
                return await target_addon.convert_settings_overrides(
                    self.version, overrides=data
                )
            except Exception:
                log_traceback(f"Unable to migrate {self} settings to {as_version}")
                return {}

        return data

    async def get_project_site_overrides(
        self,
        project_name: str,
        user_name: str,
        site_id: str,
        as_version: str | None = None,
    ) -> dict[str, Any]:
        """Load the site overrides from the database."""

        res = await Postgres.fetch(
            f"""
            SELECT data FROM project_{project_name}.project_site_settings
            WHERE addon_name = $1 AND addon_version = $2
            AND user_name = $3 AND site_id = $4
            """,
            self.definition.name,
            self.version,
            user_name,
            site_id,
        )
        data = {}
        if res:
            data = res[0]["data"]

        if as_version and as_version != self.version:
            target_addon = self.definition.get(as_version)
            if target_addon is None:
                raise BadRequestException(
                    f"Unable to parse {self} settings as {as_version}"
                    "Target addon not found"
                )

            try:
                return await target_addon.convert_settings_overrides(
                    self.version, overrides=data
                )
            except Exception:
                log_traceback(f"Unable to migrate {self} settings to {as_version}")
                return {}
        return data

    # Get settings and apply the overrides

    async def get_studio_settings(
        self,
        variant: str = "production",
        as_version: str | None = None,
    ) -> BaseSettingsModel | None:
        """Return the addon settings with the studio overrides.

        You shouldn't override this method, unless absolutely necessary.
        """

        if as_version and as_version != self.version:
            try:
                settings = await self.definition[as_version].get_default_settings()
            except KeyError:
                raise NotFoundException(
                    f"Version {as_version} does not exists"
                ) from None
        else:
            settings = await self.get_default_settings()
        if settings is None:
            return None  # this addon has no settings at all
        overrides = await self.get_studio_overrides(
            variant=variant, as_version=as_version
        )
        if overrides:
            settings = apply_overrides(settings, overrides)
            settings._has_studio_overrides = True

        return settings

    async def get_project_settings(
        self,
        project_name: str,
        variant: str = "production",
        as_version: str | None = None,
    ) -> BaseSettingsModel | None:
        """Return the addon settings with the studio and project overrides.

        You shouldn't override this method, unless absolutely necessary.
        """

        settings = await self.get_studio_settings(
            variant=variant,
            as_version=as_version,
        )
        if settings is None:
            return None  # this addon has no settings at all
        has_studio_overrides = settings._has_studio_overrides

        project_overrides = await self.get_project_overrides(
            project_name, variant=variant, as_version=as_version
        )
        if project_overrides:
            settings = apply_overrides(settings, project_overrides)
            settings._has_project_overrides = True
        settings._has_studio_overrides = has_studio_overrides
        return settings

    async def get_project_site_settings(
        self,
        project_name: str,
        user_name: str,
        site_id: str,
        variant: str = "production",
        as_version: str | None = None,
    ) -> BaseSettingsModel | None:
        """Return the addon settings with the studio, project and site overrides.

        You shouldn't override this method, unless absolutely necessary.
        """
        settings = await self.get_project_settings(
            project_name, variant=variant, as_version=as_version
        )
        if settings is None:
            return None
        has_project_overrides = settings._has_project_overrides
        has_studio_overrides = settings._has_studio_overrides
        site_overrides = await self.get_project_site_overrides(
            project_name, user_name, site_id, as_version=as_version
        )
        if site_overrides:
            settings = apply_overrides(settings, site_overrides)
            settings._has_site_overrides = True
        settings._has_project_overrides = has_project_overrides
        settings._has_studio_overrides = has_studio_overrides
        return settings

    async def get_site_settings(
        self,
        user_name: str,
        site_id: str,
    ) -> dict[str, Any] | None:
        site_settings_model = self.get_site_settings_model()
        if site_settings_model is None:
            return None

        data = {}
        query = """
            SELECT data FROM site_settings
            WHERE site_id = $1 AND addon_name = $2
            AND addon_version = $3 AND user_name = $4
        """
        async for row in Postgres.iterate(
            query,
            site_id,
            self.name,
            self.version,
            user_name,
        ):
            data = row["data"]
            break
        else:
            return None

        return site_settings_model(**data).dict()

    async def on_settings_changed(
        self,
        old_settings: BaseSettingsModel,
        new_settings: BaseSettingsModel,
        variant: str = "production",
        project_name: str | None = None,  # for project overrides
        site_id: str | None = None,  # for site overrides
        user_name: str | None = None,  # for site overrides
    ) -> None:
        """Hook called when the addon settings are changed."""
        pass

    #
    # Overridable settings-related methods
    #

    async def get_sso_options(self, base_url: str) -> list[SSOOption] | None:
        """Return a list of SSO options provided by the addon"""
        return None

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

    async def convert_settings_overrides(
        self,
        source_version: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert settings overrides from a previous version.

        By default, migrate_setting helper is used to perform a "best guess"
        in order to create compatible version of the settings, but you may
        override this method and set up a custom logic.

        Result should be a dictionary cotaining override data (same as they are
        stored in the database)

        You may extend migrate_settings functionality using custom parsers.

        Assuming a field "submodel.info" (where . is used for nesting),
        has been changed from `str` to `list[str]`, you may use:

        ```python
        async def convert_str_to_list_str(value: str | list[str]) -> list[str]:
            if isinstance(value, str):
                return [value]
            elif isinstance(value, list):
                return value

        result = migrate_settings(
            overrides,
            new_model_class=self.get_settings_model(),
            custom_conversions={
                "submodel.info": convert_str_to_list_str
            }
        )

        ```
        """

        model_class = self.get_settings_model()
        if model_class is None:
            return {}
        defaults = await self.get_default_settings()
        assert defaults is not None
        return migrate_settings_overrides(
            overrides,
            new_model_class=model_class,
            defaults=defaults.dict(),
        )

    async def get_app_host_names(self) -> list[str]:
        """Return a list of application host names that the addon uses.

        Addon may reimplment this method to return a list of host names that
        the addon uses.

        By default, it returns a single host name from the
        addon's attributes. If the addon uses multiple host names, you should
        override this method.
        """

        if self.app_host_name is None:
            return []
        return [self.app_host_name]

    #
    # Actions
    #

    async def get_simple_actions(
        self,
        project_name: str | None = None,
        variant: str = "production",
    ) -> list[SimpleActionManifest]:
        """Return a list of simple actions provided by the addon"""
        return []

    async def get_dynamic_actions(
        self,
        context: ActionContext,
        variant: str = "production",
    ) -> list[DynamicActionManifest]:
        """Return a list of dynamic actions provided by the addon"""
        return []

    # TODO: do we need this?
    # async def get_all_actions(
    #     self,
    #     context: ActionContext,
    # ) -> list[BaseActionManifest]:
    #     """Return a list of all actions provided by the addon"""
    #     return await self.get_simple_actions()

    async def execute_action(
        self,
        executor: ActionExecutor,
    ) -> ExecuteResponseModel:
        """Execute an action provided by the addon"""
        raise ValueError(f"Unknown action: {executor.identifier}")
