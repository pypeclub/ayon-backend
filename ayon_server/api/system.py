import os
import signal

from ayon_server.events import EventStream
from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger


def restart_server():
    """Force the server to restart.

    This is usually called from ayon_server.api.messaging,
    when `server.restart_requested` event is triggered.
    """
    logger.warning("Server is restarting")

    # Send a SIGHUP to the parent process (gunicorn/granian) to request a reload
    # Gunicorn will restart the server when it receives this signal,
    # but it won't quit itself, so the container will keep running.

    os.kill(os.getppid(), signal.SIGHUP)


async def require_server_restart(
    user_name: str | None = None,
    reason: str | None = None,
):
    """Mark the server as requiring a restart.

    This will notify the administrators that the server needs to be restarted.
    When the server is ready to restart, the administrator can use
    restart_server (using /api/system/restart) to trigger server.restart_requested
    event, which (captured by messaging) will trigger restart_server function
    and restart the server.
    """

    topic = "server.restart_required"
    if reason is None:
        reason = "Server restart is required"

    await EventStream.dispatch(
        topic,
        hash=topic,
        description=reason,
        user=user_name,
        reuse=True,
    )


async def clear_server_restart_required():
    """Clear the server restart required flag.

    This will clear the server.restart_requested event, and the server
    will not restart until the flag is set again.

    This is called from ayon_server.api.server, when the server is actually
    restarted.
    """

    await Postgres.execute("DELETE FROM events WHERE hash = 'server.restart_required'")
