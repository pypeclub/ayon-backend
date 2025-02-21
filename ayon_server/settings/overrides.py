import copy
from typing import Any

from ayon_server.logging import logger
from ayon_server.settings.common import BaseSettingsModel
from ayon_server.utils import dict_remove_path


def apply_overrides(
    settings: BaseSettingsModel,
    overrides: dict[str, Any],
) -> BaseSettingsModel:
    """Take a system settings object and apply the overrides to it.

    Overrides are a dictionary of the same structure as the settings object,
    but only the values that have been overridden are included (which is
    the way overrides are stored in the database).
    """
    result: dict[str, Any] = {}

    def crawl(
        obj: BaseSettingsModel,
        override: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        for name, _field in obj.__fields__.items():
            child = getattr(obj, name)
            if isinstance(child, BaseSettingsModel):
                target[name] = {}
                crawl(child, override.get(name, {}), target[name])
            else:
                # Naive types
                if name in override:
                    try:
                        # TODO: WTF??
                        type(child)(override[name])
                    except ValueError:
                        logger.warning(f"Invalid value for {name}: {override[name]}")
                        continue
                    except TypeError:
                        # This is okay
                        pass

                    target[name] = override[name]
                else:
                    target[name] = child

    crawl(settings, overrides, result)
    return settings.__class__(**result)


def list_overrides(
    obj: BaseSettingsModel,
    override: dict[str, Any],
    crumbs: list[str] | None = None,
    level: str = "studio",
    in_group: list[str] | None = None,
    scope: list[str] | None = None,
) -> dict[str, Any]:
    """Returns values which are overriden.

    This is used in the settings form context.
    Return a dictionary of the form:

    ```
    {
        key : {
            "path": path,
            "type": type,
            "value": value,
            "level": level,
            "inGroup": path,
            "scope": ["studio", "project", "site"]
        }
    }
    ```

    key:     idSchema of the field as used in rjsf
    path:    list of parent keys and the current key
    type:    type of the field: branch, leaf, group, array
    value:   value of the field (only present on leaves)
    level:   source of the override: studio, project or site
    inGroup: path of the group the field is in
    scope:   list of the scopes the field is in ["studio", "project", "site"]
    """

    result = {}

    if crumbs is None:
        crumbs = []
        root = "root"
    else:
        root = "root_" + "_".join(crumbs)

    for name, field in obj.__fields__.items():
        child = getattr(obj, name)
        path = f"{root}_{name}"
        chcrumbs = [*crumbs, name]

        try:
            field_extra = field.field_info.extra
        except AttributeError:
            field_extra = {}
        _scope = field_extra.get("scope", copy.copy(scope))
        if _scope is None and root == "root":
            _scope = ["studio", "project"]

        if isinstance(child, BaseSettingsModel):
            if name in override:
                result[path] = {
                    "path": chcrumbs,
                    "type": "group" if child._isGroup else "branch",
                    "level": level,
                    "inGroup": in_group,
                    "scope": _scope,
                }
            result.update(
                list_overrides(
                    child,
                    override.get(name, {}),
                    chcrumbs,
                    level,
                    in_group=chcrumbs if child._isGroup else in_group,
                    scope=_scope,
                )
            )

        elif isinstance(child, list):
            if name in override:
                result[path] = {
                    "path": chcrumbs,
                    "type": "list",
                    "level": level,
                    "inGroup": in_group,
                    "scope": _scope,
                }

                for i, item in enumerate(child):
                    ovr = override[name][i]
                    if isinstance(item, BaseSettingsModel):
                        result.update(
                            list_overrides(
                                item,
                                ovr,
                                [*chcrumbs, f"{i}"],
                                level=level,
                                in_group=in_group or chcrumbs,
                                scope=_scope,
                            )
                        )
                    else:
                        result[f"{path}_{i}"] = {
                            "path": [*chcrumbs, f"{i}"],
                            "level": "default",
                            "value": item,
                            "inGroup": in_group or chcrumbs,
                            "scope": _scope,
                        }

        elif isinstance(child, tuple):
            if name in override:
                result[path] = {
                    "path": chcrumbs,
                    "value": override[name] if name in override else list(child),
                    "level": level if name in override else "default",
                    "inGroup": in_group,
                    "scope": _scope,
                }

        elif name in override:
            result[path] = {
                "path": chcrumbs,
                "value": override[name],
                "level": level,
                "inGroup": in_group,
                "scope": _scope,
            }

    return result


def extract_overrides(
    default: BaseSettingsModel,
    overriden: BaseSettingsModel,
    existing: dict[str, Any] | None = None,
    explicit_pins: list[list[str]] | None = None,
    explicit_unpins: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Takes two settings objects and returns the differences between them.

    This is used to store the differences in the database, so that we can
    apply them to the default settings object later.

    explicit pins and unpins are used to force the inclusion or exclusion of
    certain fields in the result. They are lists of paths to the fields that
    should be pinned or unpinned.
    """

    existing_overrides = existing or {}
    explicit_pins = explicit_pins or []
    explicit_unpins = explicit_unpins or []

    result: dict[str, Any] = {}

    def crawl(
        original_object: BaseSettingsModel,
        new_object: BaseSettingsModel,
        existing_overrides: dict[str, Any],
        target: dict[str, Any],
        path: list[str],
    ):
        for field_name in original_object.__fields__.keys():
            old_child = getattr(original_object, field_name)
            new_child = getattr(new_object, field_name)
            field_path = [*path, field_name]

            if isinstance(old_child, BaseSettingsModel) and not old_child._isGroup:
                if field_path in explicit_pins:
                    target[field_name] = new_child.dict()

                elif old_child.dict() != new_child.dict() or (
                    field_name in existing_overrides
                ):
                    target[field_name] = {}
                    crawl(
                        original_object=old_child,
                        new_object=new_child,
                        existing_overrides=existing_overrides.get(field_name, {}),
                        target=target[field_name],
                        path=field_path,
                    )
            else:
                if (
                    old_child != new_child
                    or (field_name in existing_overrides)
                    or (field_path in explicit_pins)
                ):
                    # we need to use the original object to get the default value
                    # because of the array handling
                    # old_value = original_object.dict()[field_name]
                    new_value = new_object.dict()[field_name]
                    target[field_name] = new_value

    crawl(default, overriden, existing_overrides, result, [])

    for path in explicit_unpins:
        dict_remove_path(result, path, remove_orphans=True)

    return result
