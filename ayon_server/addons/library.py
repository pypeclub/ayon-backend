import os
from collections.abc import ItemsView
from typing import Any

from ayon_server.addons.addon import BaseServerAddon
from ayon_server.addons.definition import ServerAddonDefinition
from ayon_server.config import ayonconfig
from ayon_server.exceptions import NotFoundException
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import log_traceback, logger


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
            logger.error(f"Addons directory does not exist: {addons_dir}")
            return None

        for addon_name in sorted(os.listdir(addons_dir)):
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

            self.data[definition.name] = definition
            if definition.restart_requested:
                self.restart_requested = True

    def get_addons_dir(self) -> str | None:
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

    def __getitem__(self, key) -> ServerAddonDefinition:
        return self.data[key]

    def __contains__(self, key) -> bool:
        return key in self.data

    def __iter__(self):
        return iter(self.data)

    async def get_active_versions(self) -> dict[str, dict[str, str | None]]:
        bundles = await Postgres.fetch(
            """
            SELECT name, is_production, is_staging, is_dev, data->'addons' as addons
            FROM bundles
            """
        )
        bundles_by_variant: dict[str, dict[str, Any] | None] = {
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

        res: dict[str, dict[str, str | None]] = {}
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
    ) -> dict[str, str | None]:
        """Return addon versions for passed variant."""
        active_versions = await self.get_active_versions()
        return {
            addon_name: versions.get(variant)
            for addon_name, versions in active_versions.items()
        }

    async def get_addons_by_variant(
        self, variant: str
    ) -> dict[str, BaseServerAddon | None]:
        """Return addons for passed variant."""
        output: dict[str, BaseServerAddon | None] = {}
        active_versions = await self.get_addon_versions_by_variant(variant)
        for addon_name, addon_version in active_versions.items():
            addon: BaseServerAddon | None = None
            if addon_version:
                try:
                    addon = self[addon_name][addon_version]
                except KeyError:
                    continue
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
        logger.debug("Unloading addon", addon_name, addon_version)
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
