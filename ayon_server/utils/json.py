__all__ = ["json_loads", "json_print", "json_dumps"]

import datetime
import json
from collections.abc import Callable
from typing import Any

import orjson
from pydantic import BaseModel


def isinstance_namedtuple(obj: Any) -> bool:
    """Check if an object is an instance of a named tuple."""
    return (
        isinstance(obj, tuple) and hasattr(obj, "_asdict") and hasattr(obj, "_fields")
    )


def json_default_handler(value: Any) -> Any:
    if isinstance_namedtuple(value):
        return list(value)

    if isinstance(value, BaseModel):
        value.dict()

    if isinstance(value, datetime.datetime):
        return value.isoformat()

    if isinstance(value, set):
        return list(value)

    raise TypeError(f"Type {type(value)} is not JSON serializable")


def json_loads(data: str) -> Any:
    """Load JSON data."""
    return orjson.loads(data)


def json_print(data: Any, header: str | None = None) -> None:
    """Print JSON data."""
    if header:
        print(f"\n{header}", flush=True)
    print(json.dumps(data, indent=2), flush=True)
    if header:
        print()


def json_dumps(data: Any, *, default: Callable[[Any], Any] | None = None) -> str:
    """Dump JSON data."""
    return orjson.dumps(
        data,
        default=json_default_handler,
        option=orjson.OPT_SORT_KEYS,
    ).decode()
