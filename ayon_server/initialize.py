import asyncio

import asyncpg

from ayon_server.activities import ActivityFeedEventHook
from ayon_server.events.eventstream import EventStream
from ayon_server.extensions import init_extensions
from ayon_server.helpers.project_list import build_project_list
from ayon_server.lib.postgres import Postgres
from ayon_server.lib.redis import Redis
from ayon_server.logging import logger


async def ayon_init(extensions: bool = True):
    """Initialize ayon for use with server or stand-alone tools

    This connects to the database and installs the event hooks.
    """
    retry_interval = 2

    while Postgres.pool is None:
        try:
            await Postgres.connect()
        except ConnectionRefusedError:
            logger.info("Waiting for PostgreSQL", handlers=None)
        except asyncpg.exceptions.CannotConnectNowError:
            logger.info("PostgreSQL is starting", handlers=None)
        except Exception as e:
            msg = " ".join([str(k) for k in e.args])
            logger.error(f"Unable to connect to the database ({msg})", handlers=None)

        else:  # Connection successful
            break

        logger.info(f"Retrying in {retry_interval} seconds", handlers=None)
        await asyncio.sleep(retry_interval)

    while not Redis.connected:
        try:
            await Redis.connect()
        except ConnectionError:
            logger.info("Waiting for Redis", handlers=None)
        except Exception as e:
            msg = " ".join([str(k) for k in e.args])
            logger.error(f"Unable to connect to Redis ({msg})", handlers=None)
        else:
            break

        logger.info(f"Retrying in {retry_interval} seconds", handlers=None)
        await asyncio.sleep(retry_interval)

    if extensions:
        await init_extensions()
    ActivityFeedEventHook.install(EventStream)
    await build_project_list()
