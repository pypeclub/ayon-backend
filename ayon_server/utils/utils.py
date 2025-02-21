__all__ = [
    "dict_exclude",
    "dict_remove_path",
    "batched",
    "run_blocking_coro",
]

# TODO: Move these somewhere

import asyncio
import itertools
import threading
from collections.abc import Iterable
from typing import Any


def dict_exclude(
    data: dict[Any, Any],
    keys: list[str],
    mode: str = "exact",
) -> dict[Any, Any]:
    """Return a copy of the dictionary with the specified keys removed."""
    if mode == "exact":
        return {k: v for k, v in data.items() if k not in keys}
    elif mode == "startswith":
        return {
            k: v for k, v in data.items() if not any(k.startswith(key) for key in keys)
        }
    return data


def dict_remove_path(
    data: dict[str, Any],
    path: list[str],
    remove_orphans: bool = True,
):
    """Delete a key in a nested dictionary specified by its path."""
    parents = [data]
    for key in path[:-1]:
        if key in parents[-1]:
            n = parents[-1][key]
            if isinstance(n, dict):
                parents.append(n)
            else:
                return  # Early exit if the path is invalid
        else:
            return  # Early exit if the key does not exist in the path
    if path[-1] in parents[-1]:
        del parents[-1][path[-1]]
    else:
        return  # Early exit if the final key does not exist

    if not remove_orphans:
        return

    for i, key in enumerate(reversed(path[:-1]), 1):
        if not parents[-i]:
            del parents[-i - 1][key]
        else:
            break


def batched(iterable: Iterable[Any], n: int):
    """Implement batched function to split an iterable into batches of size n

    We need this instead of itertools.batched as we need to run on Python 3.11
    """
    it = iter(iterable)
    while True:
        batch = list(itertools.islice(it, n))
        if not batch:
            break
        yield batch


def run_blocking_coro(coro) -> Any:
    result = {"output": None}

    def execute():
        loop = asyncio.new_event_loop()
        task = loop.create_task(coro())
        # asyncio.set_event_loop(loop)
        loop.run_until_complete(task)
        result["output"] = task.result()  # noqa
        loop.close()

    thread = threading.Thread(target=execute)
    thread.start()
    thread.join()
    return result["output"]
