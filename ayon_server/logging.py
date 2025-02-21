__all__ = ["logger", "log_traceback", "critical_error"]

import sys
import time
import traceback

from loguru import logger

from ayon_server.config import ayonconfig
from ayon_server.utils import indent, json_dumps


def _write_stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _serializer(message) -> None:
    record = message.record

    if ayonconfig.log_mode == "json":
        tr = {
            "file": record["file"].path,
            "line": record["line"],
            "function": record["function"],
        }
        simplified = {
            "timestamp": time.time(),
            "level": record["level"].name.lower(),
            "message": record["message"],
            **tr,
            **record["extra"],
        }
        serialized = json_dumps(simplified)
        _write_stderr(serialized)

    else:
        level = record["level"].name
        message = record["message"]
        module = record["name"]
        formatted = f"{level:<8} | {module:<25} | {message}"
        _write_stderr(formatted)
        if tb := record.get("traceback"):
            _write_stderr(indent(str(tb)))


logger.remove(0)
logger.add(_serializer, level=ayonconfig.log_level)


def log_traceback(message="Exception!", **kwargs):
    """Log the current exception traceback."""
    tb = traceback.format_exc()
    logger.error(message, traceback=tb, **kwargs)


def critical_error(message="Critical Error!", **kwargs):
    """DEPRECATED: Log a critical error message and exit the program."""
    logger.critical(message, **kwargs)
    logger.error("Exiting program.")
    sys.exit(1)
