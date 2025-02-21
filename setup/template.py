import copy
import os
import sys
from base64 import b64decode
from pathlib import Path
from typing import Any

import httpx

from ayon_server.config import ayonconfig
from ayon_server.logging import log_traceback, logger
from ayon_server.utils import json_loads

TEMPLATE_ENV = "AYON_SETTINGS_TEMPLATE"

# Defaults which should allow Ayon server to run out of the box

DEFAULT_TEMPLATE: dict[str, Any] = {
    "addons": {},
    "settings": {},
    "users": [],
    "roles": [],
    "config": {},
    "initialBundle": None,
}

if ayonconfig.force_create_admin:
    DEFAULT_TEMPLATE["users"] = [
        {
            "name": "admin",
            "password": "admin",
            "fullName": "Ayon admin",
            "isAdmin": True,
        },
    ]


async def get_setup_template() -> dict[str, Any]:
    logger.info("Force install requested")
    template = copy.deepcopy(DEFAULT_TEMPLATE)

    # overrides
    template_data: dict[str, Any] = {}
    if "-" in sys.argv:
        # If an exception happens here, it will be caught by the main function
        # and the setup will be aborted
        # Since this is only possible by running the setup script manually,
        # using `make setup`, we don't need to worry about the error return code

        logger.info("Reading setup file from stdin")
        raw_data = sys.stdin.read()
        template_data = json_loads(raw_data)

    elif os.path.exists("/template.json"):
        # On the other hand, setting using a mounted file could be
        # unattended and we should handle errors gracefully
        # and fallback to defaults

        logger.info("Reading setup file from /template.json")
        try:
            raw_data = Path("/template.json").read_text()
            template_data = json_loads(raw_data)
        except Exception:
            logger.warning("Invalid setup file provided. Using defaults")
        else:
            logger.debug("Setting up from /template.json")

    elif raw_template_data := os.environ.get(TEMPLATE_ENV, ""):
        # Same as above, but with environment variables

        logger.info(f"Reading setup file from {TEMPLATE_ENV} env variable")
        try:
            template_data = json_loads(b64decode(raw_template_data).decode())
        except Exception:
            logger.warning(
                f"Unable to parse {TEMPLATE_ENV} env variable. Using defaults"
            )
        else:
            logger.debug(f"Setting up from {TEMPLATE_ENV} env variable")

    template.update(template_data)

    if provisioning_url := template.get("provisioningUrl"):
        logger.info(f"Provisioning from {provisioning_url}")
        headers = template.get("provisioningHeaders", {})
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(provisioning_url, headers=headers)
                response.raise_for_status()
            except Exception:
                log_traceback()
            else:
                template.update(response.json())

    return template
