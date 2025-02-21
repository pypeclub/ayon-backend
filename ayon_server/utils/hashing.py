__all__ = ["hash_data", "create_hash", "create_uuid"]

import hashlib
import random
import time
import uuid
from typing import Any

from .json import json_dumps


def hash_data(data: Any) -> str:
    """Create a SHA-256 hash from arbitrary (json-serializable) data."""
    if isinstance(data, int | float | bool | dict | list | tuple):
        data = json_dumps(data)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def create_hash() -> str:
    """Create a pseudo-random hash (used as and access token)."""
    return hash_data([time.time(), random.random()])


def create_uuid() -> str:
    """Create UUID without hyphens."""
    return uuid.uuid1().hex
