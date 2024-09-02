from typing import TYPE_CHECKING, Literal

import aiocache
import semver

from ayon_server.addons.library import AddonLibrary
from ayon_server.exceptions import NotFoundException
from ayon_server.types import Field, OPModel
from ayon_server.version import __version__ as ayon_version

from .models import BundleModel, BundlePatchModel

if TYPE_CHECKING:
    pass


def is_compatible(version: str, requirements: str) -> bool:
    conditions = requirements.split(",")
    for condition in conditions:
        condition = condition.strip()
        if not semver.match(version, condition):
            return False
    return True


class BundleIssueModel(OPModel):
    severity: Literal["error", "warning"] = Field(..., example="error")
    addon: str | None = Field(None, example="ftrack")
    message: str = Field(..., example="FTrack addon requires Core >= 1.0.0")
    required_addon: str | None = Field(None, example="core")


class CheckBundleResponseModel(OPModel):
    success: bool = False
    issues: list[BundleIssueModel] = Field(default_factory=list)

    def message(self):
        if self.success:
            return "Bundle is valid"
        for issue in self.issues:
            if issue.severity == "error":
                msg = f"{issue.addon}: {issue.message}"
        return f"Failed to validate bundle: {msg}"


async def get_active_services() -> list[dict[str, str]]:
    # TODO
    return []


@aiocache.cached()
async def get_system_addons() -> list[str]:
    result = []
    for system_addon_name, addon_definition in AddonLibrary.items():
        if addon_definition.is_system:
            result.append(system_addon_name)
    return result


async def check_bundle(
    bundle: BundleModel | BundlePatchModel,
) -> CheckBundleResponseModel:
    issues: list[BundleIssueModel] = []

    if bundle.addons is None:
        return CheckBundleResponseModel(success=True)

    for system_addon_name in await get_system_addons():
        if not bundle.addons.get(system_addon_name):
            issues.append(
                BundleIssueModel(
                    severity="error",
                    addon=system_addon_name,
                    message="System addon must be included in the bundle",
                    required_addon=None,
                )
            )

    for addon_name, addon_version in bundle.addons.items():
        if addon_version is None:
            continue
        try:
            addon = AddonLibrary.addon(addon_name, addon_version)
        except NotFoundException:
            issues.append(
                BundleIssueModel(
                    severity="error",
                    addon=addon_name,
                    message=f"{addon_name} {addon_version} is not active",
                    required_addon=addon_name,
                )
            )
            continue

        if addon.compatibility is None:
            # No compatibility information available,
            # we assume it's compatible with everything.
            continue

        compat = addon.compatibility

        if compat.server_version is not None:
            # Check if the server version is compatible
            if not is_compatible(ayon_version, compat.server_version):
                msg = f"Ayon server {addon.compatibility.server_version} is required"
                issues.append(
                    BundleIssueModel(
                        severity="error",
                        addon=addon_name,
                        message=msg,
                        required_addon=None,
                    )
                )

        if compat.launcher_version is not None:
            # Check if the launcher version is compatible
            if bundle.installer_version is None:
                issues.append(
                    BundleIssueModel(
                        severity="error",
                        addon=addon_name,
                        message="Launcher is required",
                        required_addon=None,
                    )
                )
                continue

            if not is_compatible(bundle.installer_version, compat.launcher_version):
                msg = f"Launcher {compat.launcher_version} is required"
                issues.append(
                    BundleIssueModel(
                        severity="error",
                        addon=addon_name,
                        message=msg,
                        required_addon=None,
                    )
                )

        # Check for required addons.
        # If the required addon is not present, it's an error.
        # If it is present, it must match soft_required.
        # If it doesn't match compatibility, it's an warning
        # If the requirement is set to None, it must not be present.

        for r_name, r_version in (compat.required_addons or {}).items():
            b_version = bundle.addons.get(r_name)

            if b_version is None:
                if r_version is not None:
                    issues.append(
                        BundleIssueModel(
                            severity="error",
                            addon=addon_name,
                            message=f"{r_name} is required",
                            required_addon=r_name,
                        )
                    )
                else:
                    continue

            elif r_version is None:
                issues.append(
                    BundleIssueModel(
                        severity="error",
                        addon=addon_name,
                        message=f"{r_name} must not be used",
                        required_addon=r_name,
                    )
                )

            elif not is_compatible(b_version, r_version):
                msg = f"{r_name} {r_version} is required"
                issues.append(
                    BundleIssueModel(
                        severity="error",
                        addon=addon_name,
                        message=msg,
                        required_addon=r_name,
                    )
                )

        # Check for soft required addons.

        for r_name, r_version in (compat.soft_required_addons or {}).items():
            b_version = bundle.addons.get(r_name)
            if b_version is None or r_version is None:
                # compatible addon is not required
                continue
            if not is_compatible(b_version, r_version):
                msg = f"{r_name} {r_version} is required"
                issues.append(
                    BundleIssueModel(
                        severity="error",
                        addon=addon_name,
                        message=msg,
                        required_addon=r_name,
                    )
                )

        # Check for soft compatibility. If the required addon is not present,
        # it's not an error. If it is present, it should be compatible,
        # but it's not a hard requirement. We just warn the user.

        for r_name, r_version in (compat.compatible_addons or {}).items():
            b_version = bundle.addons.get(r_name)
            if b_version is None or r_version is None:
                # compatible addon is not required
                continue

            if not is_compatible(b_version, r_version):
                msg = f"only compatible with {r_name} {r_version}"
                issues.append(
                    BundleIssueModel(
                        severity="warning",
                        addon=addon_name,
                        message=msg,
                        required_addon=r_name,
                    )
                )

    has_errors = any(issue.severity == "error" for issue in issues)
    return CheckBundleResponseModel(success=not has_errors, issues=issues)
