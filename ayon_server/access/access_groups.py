from typing import Any

from nxtools import logging

from ayon_server.access.permissions import (
    AttributeAccessList,
    EndpointsAccessList,
    FolderAccess,
    FolderAccessList,
    Permissions,
)
from ayon_server.helpers.project_list import get_project_list
from ayon_server.lib.postgres import Postgres
from ayon_server.types import normalize_to_dict


class AccessGroups:
    access_groups: dict[tuple[str, str], Permissions] = {}

    @classmethod
    async def load(cls) -> None:
        cls.access_groups = {}
        async for row in Postgres.iterate(
            "SELECT name, data FROM public.access_groups"
        ):
            cls.add_access_group(
                row["name"],
                "_",
                Permissions.from_record(row["data"]),
            )

        project_list = await get_project_list()
        for project in project_list:
            project_name = project.name
            async for row in Postgres.iterate(
                f"SELECT name, data FROM project_{project_name}.access_groups"
            ):
                cls.add_access_group(
                    row["name"],
                    project_name,
                    Permissions.from_record(row["data"]),
                )

    @classmethod
    def add_access_group(
        cls, name: str, project_name: str, permissions: Permissions
    ) -> None:
        logging.debug("Adding access_group", name)
        cls.access_groups[(name, project_name)] = permissions

    @classmethod
    def combine(
        cls, access_group_names: list[str], project_name: str = "_"
    ) -> Permissions:
        """Create aggregated permissions object for a given list of access_groups.

        If a project name is specified and there is a project-level override
        for a given access group, it will be used.
        Ohterwise a "_" (default) access group will be used.
        """

        result: dict[str, Any] | None = None

        for access_group_name in access_group_names:
            if (access_group_name, project_name) in cls.access_groups:
                access_group = cls.access_groups[(access_group_name, project_name)]
            elif (access_group_name, "_") in cls.access_groups:
                access_group = cls.access_groups[(access_group_name, "_")]
            else:
                continue

            if result is None:
                result = access_group.dict()
                if project_name != "_":
                    result.pop("studio_settings", None)
                continue

            for perm_name, value in access_group:
                if perm_name in ("studio_settings") and project_name != "_":
                    # ignore project overrides for studio settings
                    # as they don't make sense and they are just noise
                    # from the model.
                    continue

                if not value.enabled:
                    result[perm_name] = {"enabled": False}
                    continue
                elif not result[perm_name]["enabled"]:
                    continue

                if perm_name in ["project_settings", "studio_settings"]:
                    result[perm_name]["addons"] = list(
                        set(result[perm_name].get("addons", [])) | set(value.addons)
                    )

                    if perm_name == "project_settings":
                        result[perm_name]["anatomy_update"] = (
                            result[perm_name].get("anatomy_update", False)
                            or value.anatomy_update
                        )

                elif perm_name in ("create", "read", "update", "delete"):
                    # TODO: deduplicate
                    assert isinstance(value, FolderAccessList)
                    result[perm_name]["access_list"] = list(
                        {
                            FolderAccess(**normalize_to_dict(r))
                            for r in result[perm_name].get("access_list", [])
                        }
                        | set(value.access_list)
                    )

                elif perm_name in ("attrib_read", "attrib_write"):
                    assert isinstance(value, AttributeAccessList)
                    result[perm_name]["attributes"] = list(
                        set(result[perm_name].get("attributes", []))
                        | set(value.attributes)
                    )
                elif perm_name == "endpoints":
                    assert isinstance(value, EndpointsAccessList)
                    result[perm_name]["endpoints"] = list(
                        set(result[perm_name].get("endpoints", []))
                        | set(value.endpoints)
                    )

        if not result:
            return Permissions()
        return Permissions(**result)
