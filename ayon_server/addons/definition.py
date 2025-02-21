import os
from typing import TYPE_CHECKING

import semver
import yaml

from ayon_server.addons.addon import METADATA_KEYS, BaseServerAddon
from ayon_server.helpers.modules import classes_from_module, import_module
from nxtools import log_traceback, logging, slugify

if TYPE_CHECKING:
    from ayon_server.addons.library import AddonLibrary


class ServerAddonDefinition:
    title: str | None = None
    app_host_name: str | None = None

    def __init__(self, library: "AddonLibrary", addon_dir: str):
        self.library = library
        self.addon_dir = addon_dir
        self.restart_requested = False
        self._versions: dict[str, BaseServerAddon] | None = None

        if not self.versions:
            logging.warning(f"Addon {self.name} has no versions")
            return

        for version in self.versions.values():
            if self.app_host_name is None:
                self.app_host_name = version.app_host_name

            if version.name != self.name:
                raise ValueError(
                    f"Addon {self.name} has version {version.version} with "
                    f"mismatched name {version.name} != {self.name}"
                )

            self.title = version.title  # Use the latest title

    @property
    def dir_name(self) -> str:
        return os.path.split(self.addon_dir)[-1]

    @property
    def name(self) -> str:
        for version in self.versions.values():
            return version.name
        return os.path.split(self.addon_dir)[-1]

    @property
    def friendly_name(self) -> str:
        """Return a friendly (human readable) name of the addon."""
        if self.versions:
            if self.title:
                return self.title
            if hasattr(self, "name"):
                return self.name.capitalize()
        return f"[{self.dir_name.capitalize()}]"

    @property
    def versions(self) -> dict[str, BaseServerAddon]:
        """Return a list of addon versions.

        The list is a dictionary with version names as keys and addon
        instances as values. Addons are initialized when this property
        is accessed for the first time, which should happen
        right after server startup.
        """
        if self._versions is None:
            self._versions = {}
            for version_name in os.listdir(self.addon_dir):
                version_dir = os.path.join(self.addon_dir, version_name)

                try:
                    if os.path.exists(os.path.join(version_dir, "__init__.py")):
                        self.init_legacy_addon(version_dir)
                        continue

                    for filename in ["package.py", "package.yml", "package.yaml"]:
                        if os.path.exists(os.path.join(version_dir, filename)):
                            self.init_addon(version_dir)
                            break

                except AssertionError as e:
                    logging.error(f"Failed to initialize addon {version_dir}: {e}")
                except Exception:
                    log_traceback(f"Failed to initialize addon {version_dir}")

        return self._versions

    def init_addon(self, addon_dir: str):
        """Initialize the addon using package.py/package.yml/package.yaml file.

        package file must contain at least name and version keys.
        additional metadata (title, services) are optional, may be as well
        defined in the addon class itself, but it is recommended to keep
        them in the package file for better readability and maintainability.
        """
        vname = slugify(f"{self.dir_name}-{os.path.split(addon_dir)[-1]}")

        server_module_path = os.path.join(addon_dir, "server", "__init__.py")
        package_path = os.path.join(addon_dir, "package.py")

        # addon metadata
        metadata = {}

        if os.path.exists(package_path):
            package_module_name = f"{vname}-package"
            package_module = import_module(package_module_name, package_path)
            for key in METADATA_KEYS:
                if hasattr(package_module, key):
                    metadata[key] = getattr(package_module, key)

        elif os.path.exists(os.path.join(addon_dir, "package.yml")):
            with open(os.path.join(addon_dir, "package.yml")) as f:
                metadata = yaml.safe_load(f)

        elif os.path.exists(os.path.join(addon_dir, "package.yaml")):
            with open(os.path.join(addon_dir, "package.yaml")) as f:
                metadata = yaml.safe_load(f)

        assert "name" in metadata, f"Addon {vname} is missing name"
        assert "version" in metadata, f"Addon {metadata['name']} is missing version"

        # when the addon directory is a git repository,
        # append -git to the version this is useful for development
        # the server-part of the addon directly from the git repository
        # on the server

        if os.path.exists(os.path.join(addon_dir, ".git")):
            version = metadata["version"].split("-")[0]
            version = version.split("+")[0]
            version += "+git"
            metadata["version"] = version

        try:
            semver.VersionInfo.parse(metadata["version"])
        except ValueError:
            raise AssertionError(
                f"Addon {metadata['name']} has invalid version {metadata['version']}"
            )

        # Import the server module

        module = import_module(vname, server_module_path)

        # And initialize the addon

        if self._versions is None:
            self._versions = {}

        for Addon in classes_from_module(BaseServerAddon, module):
            addon = Addon(self, addon_dir=addon_dir, **metadata)
            if addon.restart_requested:
                logging.warning(
                    f"{addon}requested server restart during initialization."
                )
                self.restart_requested = True
            self._versions[metadata["version"]] = addon

    def init_legacy_addon(self, addon_dir: str):
        """Initialize old-style addon with __init__.py in the root directory.

        This style is deprecated and will be removed in the future.
        New style is supported since 1.0.3
        """

        mfile = os.path.join(addon_dir, "__init__.py")
        vname = slugify(f"{self.dir_name}-{os.path.split(addon_dir)[-1]}")
        module = import_module(vname, mfile)

        if self._versions is None:
            self._versions = {}

        for Addon in classes_from_module(BaseServerAddon, module):
            # legacy addons don't have metadata in the package file,
            # and they depend on class attributes.
            addon = Addon(self, addon_dir)
            addon.legacy = True
            if addon.restart_requested:
                logging.warning(
                    f"{addon} requested server restart during initialization."
                )
                self.restart_requested = True
            self._versions[Addon.version] = addon

    @property
    def latest(self) -> BaseServerAddon | None:
        if not self.versions:
            return None
        versions = list(self.versions.keys())
        max_version = max(versions, key=semver.VersionInfo.parse)
        return self.versions[max_version]

    @property
    def is_system(self) -> bool:
        for version in self.versions.values():
            if version.system:
                return True
        return False

    @property
    def addon_type(self) -> str:
        sorted_versions = sorted(
            self.versions.values(),
            key=lambda x: semver.VersionInfo.parse(x.version),
        )
        if not sorted_versions:
            return "pipeline"
        return sorted_versions[-1].addon_type

    def __getitem__(self, item) -> BaseServerAddon:
        return self.versions[item]

    def get(self, item, default=None) -> BaseServerAddon | None:
        return self.versions.get(item, default)

    def unload_version(self, version: str) -> None:
        """Unload the given version of the addon."""
        if self._versions and version in self._versions:
            del self._versions[version]
