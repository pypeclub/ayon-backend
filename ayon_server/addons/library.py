import inspect
import os
from typing import Any, ItemsView, Optional

from nxtools import log_traceback, logging

from ayon_server.addons.addon import BaseServerAddon
from ayon_server.addons.definition import ServerAddonDefinition
from ayon_server.config import ayonconfig

# from ayon_server.addons.utils import classes_from_module, import_module
from ayon_server.exceptions import NotFoundException
from ayon_server.lib.postgres import Postgres


class InitializationError(Exception):
    pass


class AddonLibrary:
    ADDONS_DIR = ayonconfig.addons_dir
    _instance = None

    @classmethod
    def getinstance(cls) -> "AddonLibrary":
        if cls._instance is None:
            cls._instance = AddonLibrary()
        return cls._instance

    def __init__(self) -> None:
        self.data: dict[str, ServerAddonDefinition] = {}
        self.broken_addons: dict[tuple[str, str], dict[str, str]] = {}
        self.restart_requested = False
        addons_dir = self.get_addons_dir()
        if addons_dir is None:
            logging.error(f"Addons directory does not exist: {addons_dir}")
            return None

        for addon_name in os.listdir(addons_dir):
            # ignore hidden directories (such as .git)
            if addon_name.startswith("."):
                continue

            addon_dir = os.path.join(addons_dir, addon_name)
            if not os.path.isdir(addon_dir):
                continue

            try:
                definition = ServerAddonDefinition(self, addon_dir)
            except Exception:
                log_traceback(f"Unable to initialize {addon_dir}")
                continue
            if not definition.versions:
                continue

            logging.debug("Initializing addon", definition.name)
            self.data[definition.name] = definition
            if definition.restart_requested:
                self.restart_requested = True

    @classmethod
    async def hot_load(
        cls, addon_name: str, addon_version: str, addon_dir: str
    ) -> None:
        elms = addon_dir.split("/")
        if len(elms) != 2:
            raise ValueError(
                f"Invalid addon directory: {addon_dir} (must be addon_name/version)"
            )

        addon_root = cls.get_addons_dir()
        if addon_root is None:
            logging.error(f"Addons root directory does not exist: {addon_root}")
            return
        full_addon_dir = os.path.join(addon_root, addon_dir)
        if not os.path.isdir(full_addon_dir):
            logging.error(f"Addon directory does not exist: {full_addon_dir}")
            return

        try:
            addon = cls.addon(addon_name, addon_version)
            logging.info(f"HotLoad: {addon} already loaded")
            return
        except NotFoundException:
            pass

        print("HotLoad", addon_name, addon_version, full_addon_dir)

        instance = cls.getinstance()
        try:
            if addon_name in instance.data:
                definition = instance[addon_name]
                if not definition.init_version(full_addon_dir):
                    raise InitializationError
            else:
                instance.data[addon_name] = ServerAddonDefinition(
                    instance, full_addon_dir, force=True
                )
                if not instance.data[addon_name].versions:
                    raise InitializationError
        except InitializationError:
            logging.error(f"Failed to initialize {full_addon_dir}")
            instance.broken_addons[(addon_name, addon_version)] = {
                "reason": "Failed to initialize"
            }

        addon = cls.addon(addon_name, addon_version)
        if inspect.iscoroutinefunction(addon.pre_setup):
            await addon.pre_setup()
        else:
            addon.pre_setup()

        if inspect.iscoroutinefunction(addon.setup):
            await addon.setup()
        else:
            addon.setup()
        logging.info(f"HotLoad: {addon} loaded")

    @staticmethod
    def get_addons_dir() -> str | None:
        for d in [ayonconfig.addons_dir, "addons"]:
            if not os.path.isdir(d):
                continue
            return d
        return None

    @classmethod
    def addon(cls, name: str, version: str) -> BaseServerAddon:
        """Return an instance of the given addon.

        Raise NotFoundException if the addon is not found.
        """

        instance = cls.getinstance()
        if (definition := instance.data.get(name)) is None:
            raise NotFoundException(f"Addon {name} does not exist")
        if (addon := definition.versions.get(version)) is None:
            raise NotFoundException(f"Addon {name} version {version} does not exist")
        return addon

    @classmethod
    def items(cls) -> ItemsView[str, ServerAddonDefinition]:
        instance = cls.getinstance()
        return instance.data.items()

    @classmethod
    def get(cls, key: str, default: Any = None) -> ServerAddonDefinition | None:
        instance = cls.getinstance()
        return instance.data.get(key, default)

    def __getitem__(self, key: str) -> ServerAddonDefinition:
        return self.data[key]

    def __contains__(self, key) -> bool:
        return key in self.data

    def __iter__(self):
        return iter(self.data)

    async def get_active_versions(self) -> dict[str, dict[str, Optional[str]]]:
        bundles = await Postgres.fetch(
            """
            SELECT name, is_production, is_staging, is_dev, data->'addons' as addons
            FROM bundles
            """
        )
        bundles_by_variant: dict[str, Optional[dict[str, Any]]] = {
            "production": None,
            "staging": None,
        }
        for bundle in bundles:
            if bundle["is_dev"]:
                bundles_by_variant[bundle["name"]] = bundle
                continue

            if bundle["is_production"]:
                bundles_by_variant["production"] = bundle

            if bundle["is_staging"]:
                bundles_by_variant["staging"] = bundle

        res: dict[str, dict[str, Optional[str]]] = {}
        for addon_name in self.data.keys():
            addon_info = res.setdefault(addon_name, {})
            for variant, bundle in bundles_by_variant.items():
                addon_version = None
                if bundle is not None:
                    addon_version = bundle["addons"].get(addon_name)
                addon_info[variant] = addon_version
        return res

    async def get_addon_versions_by_variant(
        self, variant: str
    ) -> dict[str, Optional[str]]:
        """Return addon versions for passed variant."""
        active_versions = await self.get_active_versions()
        return {
            addon_name: versions.get(variant)
            for addon_name, versions in active_versions.items()
        }

    async def get_addons_by_variant(
        self, variant: str
    ) -> dict[str, Optional[BaseServerAddon]]:
        """Return addons for passed variant."""
        output: dict[str, Optional[BaseServerAddon]] = {}
        active_versions = await self.get_addon_versions_by_variant(variant)
        for addon_name, addon_version in active_versions.items():
            addon: Optional[BaseServerAddon] = None
            if addon_version:
                addon = self[addon_name][addon_version]
            output[addon_name] = addon
        return output

    async def get_addon_by_variant(
        self, addon_name: str, variant: str
    ) -> BaseServerAddon | None:
        """Return instance of the addon by variant."""
        active_versions = await self.get_active_versions()
        if addon_name not in active_versions:
            return None
        addon_version = active_versions[addon_name].get(variant)
        if addon_version is None:
            return None
        return self[addon_name][addon_version]

    async def get_production_addon(self, addon_name: str) -> BaseServerAddon | None:
        """Return a production instance of the addon."""
        return await self.get_addon_by_variant(addon_name, "production")

    async def get_staging_addon(self, addon_name: str) -> BaseServerAddon | None:
        """Return a staging instance of the addon."""
        return await self.get_addon_by_variant(addon_name, "staging")

    @classmethod
    def unload_addon(
        cls, addon_name: str, addon_version: str, reason: dict[str, str] | None = None
    ) -> None:
        instance = cls.getinstance()
        if reason is not None:
            instance.broken_addons[(addon_name, addon_version)] = reason
        definition = instance.data.get(addon_name)
        if definition is None:
            return
        logging.info("Unloading addon", addon_name, addon_version)
        definition.unload_version(addon_version)

    @classmethod
    def is_broken(cls, addon_name: str, addon_version: str) -> dict[str, Any] | None:
        instance = cls.getinstance()
        if summary := instance.broken_addons.get((addon_name, addon_version), None):
            return summary
        return None

    def get_broken_versions(self, addon_name: str) -> dict[str, dict[str, str]]:
        return {
            version: summary
            for (name, version), summary in self.broken_addons.items()
            if name == addon_name
        }
