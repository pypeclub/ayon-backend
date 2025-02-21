from typing import Any

from ayon_server.lib.postgres import Postgres
from nxtools import logging

# The following parameters may be used:
# name, scope, type, title, default, example,
# gt, lt, regex, min_len, max_len, description
#
# Scope and type are required.
# All project attributes should have a default value
#
# Available types:
#   integer, float, string, boolean, list_of_strings
#
# Possible validation rules:
# - gt (for integers and floats)
# - lt (for integers and floats)
# - regex (for strings)


DEFAULT_ATTRIBUTES: dict[str, dict[str, Any]] = {
    "priority": {
        "scope": "P, F, T",
        "type": "string",
        "title": "Priority",
        "default": "normal",
        "enum": [
            {
                "value": "urgent",
                "label": "Urgent",
                "color": "#FF8585",
                "icon": "keyboard_double_arrow_up",
            },
            {
                "value": "high",
                "label": "High",
                "color": "#FFAD66",
                "icon": "keyboard_arrow_up",
            },
            {
                "value": "normal",
                "label": "Normal",
                "color": "#9AC0E7",
                "icon": "check_indeterminate_small",
            },
            {
                "value": "low",
                "label": "Low",
                "color": "#9FA7B1",
                "icon": "keyboard_arrow_down",
            },
        ],
    },
    "fps": {
        "scope": "P, F, V, R, T",
        "type": "float",
        "title": "FPS",
        "default": 25,
        "example": 25,
        "gt": 0,
        "description": "Frame rate",
    },
    "resolutionWidth": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Width",
        "default": 1920,
        "example": 1920,
        "gt": 0,
        "lt": 50000,
        "description": "Horizontal resolution",
    },
    "resolutionHeight": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Height",
        "default": 1080,
        "example": 1080,
        "gt": 0,
        "lt": 50000,
        "description": "Vertical resolution",
    },
    "pixelAspect": {
        "scope": "P, F, V, R, T",
        "type": "float",
        "title": "Pixel aspect",
        "gt": 0,
        "default": 1.0,
        "example": 1.0,
    },
    "clipIn": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Clip In",
        "default": 1,
        "example": 1,
    },
    "clipOut": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Clip Out",
        "default": 1,
        "example": 1,
    },
    "frameStart": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Start frame",
        "default": 1001,
        "example": 1001,
    },
    "frameEnd": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "End frame",
        "default": 1001,
    },
    "handleStart": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Handle start",
        "default": 0,
    },
    "handleEnd": {
        "scope": "P, F, V, R, T",
        "type": "integer",
        "title": "Handle end",
        "default": 0,
    },
    "fullName": {
        "scope": "U",
        "type": "string",
        "title": "Full name",
        "example": "Jane Doe",
    },
    "email": {
        "scope": "U",
        "type": "string",
        "title": "E-Mail",
        "example": "jane.doe@ayon.cloud",
    },
    "avatarUrl": {
        "scope": "U",
        "type": "string",
        "title": "Avatar URL",
    },
    "developerMode": {
        "scope": "U",
        "type": "boolean",
        "title": "Developer mode",
        "example": True,
    },
    "productGroup": {
        "scope": "S",
        "type": "string",
        "title": "Product group",
    },
    "intent": {
        "scope": "V",
        "type": "string",
        "title": "Intent",
    },
    "machine": {
        "scope": "V",
        "type": "string",
        "title": "Machine",
    },
    "source": {
        "scope": "V",
        "type": "string",
        "title": "Source",
    },
    "comment": {
        "scope": "V",
        "type": "string",
        "title": "Comment",
    },
    "site": {
        "scope": "V",
        "type": "string",
        "title": "Site",
        "example": "workstation42",
    },
    "families": {
        "scope": "V",
        "type": "list_of_strings",
        "title": "Families",
    },
    "colorSpace": {
        "scope": "V",
        "type": "string",
        "title": "Color space",
        "example": "rec709",
    },
    "path": {
        "scope": "R",
        "type": "string",
        "title": "Path",
    },
    "template": {
        "scope": "R",
        "type": "string",
        "title": "Template",
    },
    "extension": {
        "scope": "R, W",
        "type": "string",
        "title": "File extension",
    },
    "startDate": {
        "scope": "P, F, T",
        "type": "datetime",
        "example": "2021-01-01T00:00:00+00:00",
        "title": "Start date",
        "description": "Date and time when the project or task or asset was started",
    },
    "endDate": {
        "scope": "P, F, T",
        "type": "datetime",
        "example": "2021-01-01T00:00:00+00:00",
        "title": "End date",
        "description": "Deadline date and time",
    },
    "description": {
        "scope": "P, F, T, S, V, R, W",
        "type": "string",
        "example": "A very nice entity",
        "title": "Description",
        "description": "Textual description of the entity",
        "inherit": False,
    },
    # "testEnum": {
    #     "scope": "P, F, V, R, T",
    #     "type": "string",
    #     "title": "Test enum",
    #     "default": "test1",
    #     "example": "test1",
    #     "enum": [
    #         {"value": "test1", "label": "Test 1"},
    #         {"value": "test2", "label": "Test 2"},
    #         {"value": "test3", "label": "Test 3"},
    #     ],
    # },
    # "testList": {
    #     "scope": "P, F, V, R, T",
    #     "type": "list_of_strings",
    #     "title": "Test LoS",
    #     "default": ["test1"],
    #     "example": ["test1", "test2"],
    #     "enum": [
    #         {"value": "test1", "label": "Test 1"},
    #         {"value": "test2", "label": "Test 2"},
    #         {"value": "test3", "label": "Test 3"},
    #     ],
    # },
}


async def deploy_attributes() -> None:
    position = 0
    for name, tdata in DEFAULT_ATTRIBUTES.items():
        try:
            scope = [
                {
                    "p": "project",
                    "u": "user",
                    "f": "folder",
                    "t": "task",
                    "s": "product",
                    "v": "version",
                    "r": "representation",
                    "w": "workfile",
                }[k.strip().lower()]
                for k in tdata["scope"].split(",")
            ]
        except KeyError:
            logging.error(f"Unknown scope specified on {name}. Skipping")
            continue

        if tdata["type"] not in [
            "string",
            "integer",
            "float",
            "boolean",
            "datetime",
            "list_of_strings",
            "list_of_integers",
        ]:
            logging.error(f"Unknown type sepecified on {name}. Skipping.")
            continue

        data = {
            "type": tdata["type"],
            "title": tdata.get("title", name.capitalize()),
        }

        if enum := tdata.get("enum"):
            data["enum"] = enum

        for key in (
            "default",
            "example",
            "regex",
            "description",
            "gt",
            "lt",
            "inherit",
        ):
            if (value := tdata.get(key)) is not None:
                data[key] = value

        # Migration from 0.1.x to 0.2.x
        await Postgres.execute(
            """
            DELETE FROM ATTRIBUTES WHERE 'subset' = ANY(scope);
            """
        )

        await Postgres.execute(
            """
            INSERT INTO public.attributes
                (name, position, scope, builtin, data)
            VALUES
                ($1, $2, $3, TRUE, $4)
            ON CONFLICT (name) DO UPDATE
            SET
                position = EXCLUDED.position,
                scope = EXCLUDED.scope,
                builtin = EXCLUDED.builtin,
                data = EXCLUDED.data
            """,
            name,
            position,
            scope,
            data,
        )
        position += 1
