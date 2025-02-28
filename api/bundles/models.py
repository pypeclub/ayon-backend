from datetime import datetime
from typing import Any

from ayon_server.types import NAME_REGEX, Field, OPModel, Platform

dependency_packages_meta: dict[str, Any] = {
    "title": "Dependency packages",
    "description": "mapping of platform:dependency_package_filename",
    "example": {
        "windows": "a_windows_package123.zip",
        "linux": "a_linux_package123.zip",
        "darwin": "a_mac_package123.zip",
    },
}


class BaseBundleModel(OPModel):
    pass


class AddonDevelopmentItem(OPModel):
    enabled: bool = Field(
        True, example=False, description="Enable/disable addon development"
    )
    path: str = Field(
        "", example="/path/to/addon", description="Path to addon directory"
    )


class BundleModel(BaseBundleModel):
    """
    Model for GET and POST requests
    """

    name: str = Field(
        ...,
        title="Name",
        description="Name of the bundle",
        example="my_superior_bundle",
        regex=NAME_REGEX,
    )

    created_at: datetime = Field(
        default_factory=datetime.now,
        example=datetime.now(),
    )

    ## patchables

    # data
    addons: dict[str, str | None] = Field(
        default_factory=dict,
        title="Addons",
        example={"ftrack": "1.2.3"},
    )
    installer_version: str | None = Field(None, example="1.2.3")
    dependency_packages: dict[Platform, str | None] = Field(
        default_factory=dict,
        **dependency_packages_meta,
    )
    addon_development: dict[str, AddonDevelopmentItem] = Field(
        default_factory=dict,
        example={"ftrack": {"enabled": True, "path": "~/devel/ftrack"}},
    )

    # flags
    is_production: bool = Field(False, example=False)
    is_staging: bool = Field(False, example=False)
    is_archived: bool = Field(False, example=False)
    is_dev: bool = Field(False, example=False)
    is_project: bool = Field(False, example=False)
    active_user: str | None = Field(None, example="admin")


class BundlePatchModel(BaseBundleModel):
    addons: dict[str, str | None] | None = Field(
        None,
        title="Addons",
        example={"ftrack": None, "kitsu": "1.2.3"},
    )
    installer_version: str | None = Field(None, example="1.2.3")
    dependency_packages: dict[Platform, str | None] | None = Field(
        None,
        **dependency_packages_meta,
    )
    is_production: bool | None = Field(None, example=False)
    is_staging: bool | None = Field(None, example=False)
    is_archived: bool | None = Field(None, example=False)
    is_dev: bool | None = Field(None, example=False)
    active_user: str | None = Field(None, example="admin")
    addon_development: dict[str, AddonDevelopmentItem] | None = Field(None)


class ListBundleModel(OPModel):
    bundles: list[BundleModel] = Field(default_factory=list)
    production_bundle: str | None = Field(None, example="my_superior_bundle")
    staging_bundle: str | None = Field(None, example="my_superior_bundle")
    dev_bundles: list[str] = Field(default_factory=list)
