import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from ayon_server.config import ayonconfig
from ayon_server.helpers.project_list import get_project_list
from ayon_server.initialize import ayon_init
from ayon_server.lib.postgres import Postgres
from ayon_server.version import __version__ as server_version
from nxtools import critical_error, log_to_file, log_traceback, logging
from setup.access_groups import deploy_access_groups
from setup.attributes import deploy_attributes
from setup.initial_bundle import create_initial_bundle
from setup.template import get_setup_template
from setup.users import deploy_users


async def db_migration(has_schema: bool) -> int:
    """Migrate the database schema.

    Returns the latest migration version applied
    (or available if db is not initialized).
    That number is then saved in the database.
    """

    # For future use:
    # current_version = 0
    # if has_schema:
    #     res = await Postgres.fetch("SELECT value FROM config WHERE key = 'dbVersion'")
    #     if res:
    #         current_version = int(res[0]["value"])

    migrations_dir = "schemas/migrations"
    available_migrations = sorted(
        Path(migrations_dir).glob("*.sql"), key=lambda x: int(x.stem)
    )

    # We evaluate has_schema here rather than in the main function,
    # because we need to know the latest database version, even if
    # the schema is not present yet.
    if has_schema:
        # logging.debug(f"Current database version: {current_version}")
        logging.debug(f"Applying {len(available_migrations)} database migrations")
        migration_version = 0
        for migration in available_migrations:
            migration_version = int(migration.stem)

            # We still need to apply all migrations to ensure
            # imported project will have the same schema.

            # The following condition may be uncommented,
            # when we switch project backup to json-based,
            # database aware format.

            # Keep in mind that once this is enabled, we'll
            # also be skipping the first (0) migration, that
            # currently handles upgrades from < 1.0.0 era

            # if migration_version <= current_version:
            #    continue
            # logging.info(f"Applying migration {migration_version}")
            start_time = time.monotonic()
            try:
                await Postgres.execute(migration.read_text(), timeout=600)
            except Exception:
                log_traceback(f"Migration {migration.stem} failed")
                critical_error("Database migration failed. Setup cannot continue.")
            elapsed = time.monotonic() - start_time
            if elapsed > 1:
                msg = f"Migration {migration.stem} applied in {elapsed:.2f}s (slow...)"
                logging.debug(msg)
        return migration_version

    return int(available_migrations[-1].stem)


async def main(force: bool | None = None) -> None:
    """Main entry point for setup."""

    logging.info("Starting setup")

    await ayon_init(extensions=False)

    try:
        await Postgres.fetch("SELECT * FROM projects")
    except Exception:
        logging.warning("Database is empty")
        has_schema = False
        force_install = True
    else:
        # DB is okay, if we just checking the state,
        # do not force the setup
        has_schema = True
        if force is None:
            force_install = "--ensure-installed" not in sys.argv
        else:
            force_install = force

    if ("--with-schema" in sys.argv) or (not has_schema):
        logging.info("(re)creating database schema")

        schema = Path("schemas/schema.drop.sql").read_text()
        await Postgres.execute(schema)

    db_version = await db_migration(has_schema)

    schema = Path("schemas/schema.public.sql").read_text()
    await Postgres.execute(schema)

    # Save the current database version (latest migration applied)

    await Postgres.execute(
        """
        INSERT INTO config (key, value) VALUES ('dbVersion', $1)
        ON CONFLICT (key) DO UPDATE SET value = $1
        """,
        db_version,
    )

    # This is something we can do every time.
    # Similar to database migrations, built-in attributes
    # may change between versions, so we need to ensure
    # they are up-to-date, when the container is started.

    await deploy_attributes()

    # When the setup is started for the first time, or
    # is invoked using `make setup`, we  apply the
    # setup template.

    if force_install:
        template = await get_setup_template()

        projects: list[str] = []
        async for row in Postgres.iterate("SELECT name FROM projects"):
            projects.append(row["name"])

        users: list[dict[str, Any]] = template["users"]
        access_groups: list[dict[str, Any]] = template.get("accessGroups", [])

        await deploy_users(users, projects)
        await deploy_access_groups(access_groups)

        for name, value in template.get("secrets", {}).items():
            await Postgres.execute(
                """
                INSERT INTO secrets (name, value)
                VALUES ($1, $2)
                ON CONFLICT (name) DO UPDATE SET value = $2
                """,
                name,
                value,
            )

        for key, value in template.get("config", {}).items():
            await Postgres.execute(
                """
                INSERT INTO config (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = $2
                """,
                key,
                value,
            )

        if bundle_data := template.get("initialBundle"):
            if not isinstance(bundle_data, dict):
                logging.warning("Invalid initial bundle data")
            else:
                await create_initial_bundle(bundle_data)

    # If the server was updated to a new version,
    # save the current version in the database.

    await Postgres.execute(
        """
        INSERT INTO server_updates (version)
        VALUES ($1) ON CONFLICT (version) DO NOTHING
        """,
        server_version,
    )

    # Attributes may have changed, so we need to rebuild
    # existing hierarchies.

    from ayon_server.helpers.inherited_attributes import rebuild_inherited_attributes

    project_list = await get_project_list()
    for project in project_list:
        try:
            await rebuild_inherited_attributes(project.name)
        except Exception:
            log_traceback(
                f"Unable to rebuild attributes for {project.name}. "
                "Project may be corrupted."
            )

    logging.goodnews("Setup is finished")


if __name__ == "__main__":
    logging.user = "setup"
    if ayonconfig.log_file is not None:
        logging.add_handler(log_to_file(ayonconfig.log_file))
    try:
        asyncio.run(main())
    except Exception:
        log_traceback()
        critical_error("Setup failed")
    sys.exit(0)
