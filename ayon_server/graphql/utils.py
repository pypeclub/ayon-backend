from datetime import datetime
from typing import Any, Literal

from nxtools import logging

from ayon_server.entities.core import attribute_library
from ayon_server.entities.user import UserEntity
from ayon_server.utils import json_dumps


def parse_json_data(target_type, data):
    if not data:
        return target_type()
    result = {}
    for key in target_type.__dataclass_fields__.keys():
        if key in data:
            result[key] = data[key]
    return target_type(**result)


ATTRIB_WHITELIST = [
    "fullName",
    "avatarUrl",
]


def parse_attrib_data(
    target_type,
    own_attrib: dict[str, Any],
    user: UserEntity,
    project_name: str | None = None,
    inherited_attrib: dict[str, Any] | None = None,
    project_attrib: dict[str, Any] | None = None,
):
    """ACL agnostic attribute list parser"""

    attr_limit: list[str] | Literal["all"] = []

    if user.is_manager:
        attr_limit = "all"
    elif (perms := user.permissions(project_name)) is None:
        attr_limit = []  # This shouldn't happen
    elif perms.attrib_read.enabled:
        attr_limit = perms.attrib_read.attributes
    else:
        attr_limit = "all"

    if attr_limit != "all":
        for k in ATTRIB_WHITELIST:
            if k not in attr_limit:
                attr_limit.append(k)

    data = own_attrib or {}
    if inherited_attrib is not None:
        for key in attribute_library.inheritable_attributes():
            if data.get(key) is not None:
                continue
            if key in inherited_attrib:
                data[key] = inherited_attrib[key]

    if project_attrib is not None:
        for key in attribute_library.inheritable_attributes():
            if data.get(key) is not None:
                continue
            if key in project_attrib:
                data[key] = project_attrib[key]

    if not data:
        return target_type()
    result = {}
    expected_keys = target_type.__dataclass_fields__.keys()
    for key in expected_keys:
        if key in data:
            if attr_limit == "all" or key in attr_limit:
                value = data[key]
                if attribute_library.by_name(key)["type"] == "datetime":
                    value = datetime.fromisoformat(value)
                result[key] = value
    return target_type(**result)


def parse_data(data: Any) -> str:
    """Normalize entity.data field to a JSON string

    Ensure data is a dictionary.
    """
    if not data:
        return "{}"
    if isinstance(data, dict):
        return json_dumps(data)
    else:
        logging.warning(f"Invalid data type: {type(data)}")
        return "{}"
