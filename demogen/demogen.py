import asyncio
import datetime
import hashlib
import random
import time
from typing import Any

from ayon_server.entities import (
    FolderEntity,
    ProductEntity,
    ProjectEntity,
    RepresentationEntity,
    TaskEntity,
    VersionEntity,
    WorkfileEntity,
)
from ayon_server.lib.postgres import Connection, Postgres
from ayon_server.logging import logger
from ayon_server.utils import create_uuid, dict_exclude
from demogen.generators import generators
from setup.attributes import DEFAULT_ATTRIBUTES

VERSIONS_PER_PRODUCT = 5


def get_random_md5():
    """Get random md5 hash."""
    return hashlib.md5(str(random.random()).encode("utf-8")).hexdigest()


def random_datetime_interval(start, end):
    delta = end - start
    int_delta = delta.total_seconds()
    # subtract 86400 seconds (1 day) to ensure the interval lasts at least one day
    random_offset = random.uniform(0, int_delta - 86400)
    interval_start = start + datetime.timedelta(
        seconds=random_offset,
    )
    interval_end = interval_start + datetime.timedelta(days=1)
    if interval_end > end:
        return (start, start + datetime.timedelta(days=1))
    return (interval_start, interval_end)


class DemoGen:
    def __init__(self):
        self.folder_count = 0
        self.product_count = 0
        self.version_count = 0
        self.representation_count = 0
        self.task_count = 0
        self.workfile_count = 0
        self._users = []

    async def get_random_user(self) -> str:
        if not self._users:
            async for row in Postgres.iterate("SELECT name FROM users"):
                self._users.append(row["name"])
            if not self._users:
                self.users = ["artist", "editor", "admin"]
        return random.choice(self._users)

    async def populate(
        self,
        project_name: str,
        hierarchy: list[dict[str, Any]],
    ) -> None:
        start_time = time.monotonic()
        self.project_name = project_name
        self.project = await ProjectEntity.load(project_name)

        logger.info(f"Creating folders for project {project_name}")

        tasks = []
        for folder_data in hierarchy:
            tasks.append(self.create_branch(**folder_data))

        await asyncio.gather(*tasks)
        logger.info("Refreshing views")
        await Postgres.execute(
            f"""
            REFRESH MATERIALIZED VIEW project_{self.project.name}.hierarchy;
            REFRESH MATERIALIZED VIEW project_{self.project.name}.version_list;
            """
        )

        elapsed_time = time.monotonic() - start_time
        logger.info(f"{self.folder_count} folders created")
        logger.info(f"{self.product_count} product created")
        logger.info(f"{self.version_count} versions created")
        logger.info(f"{self.representation_count} representations created")
        logger.info(f"{self.task_count} tasks created")
        logger.info(f"{self.workfile_count} workfiles created")
        logger.info(f"Project {self.project_name} demo in {elapsed_time:.2f} seconds")

    def get_entity_tags(self):
        """return a list of random tags for entity"""
        tags = [tag["name"] for tag in self.project.tags]
        return random.sample(tags, random.randint(0, 5))

    def get_entity_status(self, done=False):
        """return a random status for entity"""
        statuses = list(self.project.statuses)
        if done:
            statuses = [s for s in statuses if s["state"] == "done"]
        status = random.choice(self.project.statuses)
        return status["name"]

    async def create_branch(self, **kwargs: Any) -> None:
        async with Postgres.acquire() as conn:
            async with conn.transaction():
                folder = await self.create_folder(conn, **kwargs)
                await folder.commit(conn)

    async def create_folder(
        self,
        conn: Connection,
        parent_id: str | None = None,
        parents: list[str] = [],
        **kwargs: Any,
    ) -> FolderEntity:
        self.folder_count += 1
        if self.folder_count % 100 == 0:
            logger.debug(f"{self.folder_count} folders created")

        # Propagate project attributes
        attrib = kwargs.get("attrib", {})
        if kwargs.get("folder_type") is None:
            # Use explicit attributes inherited from the project settings
            # For folder-folders
            for key, value in self.project.attrib.dict().items():
                if key in attrib:
                    continue
                attrib[key] = value
        kwargs["attrib"] = attrib

        for s in kwargs.get("_products", []):
            for r in s.get("_representations", []):
                if (tpl := r.get("template")) is not None:
                    if "{frame}" in tpl:
                        kwargs["attrib"]["frameStart"] = self.project.attrib.frameStart
                        kwargs["attrib"]["frameEnd"] = self.project.attrib.frameEnd

        payload = {
            "parent_id": parent_id,
            "tags": self.get_entity_tags(),
            "status": self.get_entity_status(),
            **dict_exclude(kwargs, ["_", "parentId"], mode="startswith"),
        }
        folder = FolderEntity(
            project_name=self.project_name,
            payload=payload,
        )
        await folder.save(conn)
        folder.parents = parents  # type: ignore

        tasks = {}

        for task in kwargs.get("_tasks", []):
            assignees = []
            # reduce the chance assignees are populated:
            if random.random() < 0.4:
                _ = await self.get_random_user()
                for _ in range(len(self._users)):
                    user = await self.get_random_user()
                    if user not in assignees:
                        assignees.append(user)
                    if len(assignees) == 2:
                        break

            task["assignees"] = assignees
            task_entity = await self.create_task(
                conn,
                folder=folder,
                parents=parents + [folder.name],
                **task,
            )
            tasks[task_entity.name] = task_entity.id

        for product in kwargs.get("_products", []):
            await self.create_product(conn, folder, tasks=tasks, **product)

        if "_children" in kwargs:
            if isinstance(kwargs["_children"], str):
                async for child in generators[kwargs["_children"]](kwargs):
                    await self.create_folder(
                        conn, folder.id, parents=parents + [folder.name], **child
                    )
            elif isinstance(kwargs["_children"], list):
                for child in kwargs["_children"]:
                    await self.create_folder(
                        conn, folder.id, parents=parents + [folder.name], **child
                    )
        return folder

    async def create_product(
        self,
        conn: Connection,
        folder: FolderEntity,
        tasks,
        **kwargs,
    ) -> ProductEntity:
        self.product_count += 1
        if task_name := kwargs.get("_task_link"):
            task_id = tasks.get(task_name)
            # print(f"product {kwargs['name']} linked to task_id {task_id}")
        else:
            task_id = None

        payload = {
            "folder_id": folder.id,
            "tags": self.get_entity_tags(),
            "status": self.get_entity_status(),
            **dict_exclude(kwargs, ["_"], mode="startswith"),
        }
        product = ProductEntity(
            project_name=self.project_name,
            payload=payload,
        )
        await product.save(conn)

        for i in range(1, VERSIONS_PER_PRODUCT):
            self.version_count += 1
            attrib = {"product_types": [kwargs["product_type"]]}

            for key, acfg in DEFAULT_ATTRIBUTES.items():
                if "V" not in [scope.strip() for scope in acfg["scope"].split(",")]:
                    continue

                val = folder.attrib.dict().get(key)
                if val is not None:
                    attrib[key] = val
            version = VersionEntity(
                project_name=self.project_name,
                payload={
                    "product_id": product.id,
                    "task_id": task_id,
                    "version": i,
                    "author": await self.get_random_user(),
                    "attrib": attrib,
                    "tags": self.get_entity_tags(),
                    "status": self.get_entity_status(),
                },
            )
            await version.save(conn)

            for representation in kwargs.get("_representations", []):
                await self.create_representation(
                    conn, folder, product, version, **representation
                )
        return product

    async def create_task(
        self,
        conn: Connection,
        folder: FolderEntity,
        parents: list[str],
        **kwargs: Any,
    ) -> TaskEntity:
        self.task_count += 1

        start_date, end_date = random_datetime_interval(
            self.project.attrib.startDate,
            self.project.attrib.endDate,
        )

        if end_date < datetime.datetime.now(tz=datetime.UTC):
            status = self.get_entity_status(done=True)
        else:
            status = self.get_entity_status()

        payload = {**kwargs}
        payload["folder_id"] = folder.id
        payload["attrib"] = folder.attrib.dict()
        payload["tags"] = self.get_entity_tags()
        payload["status"] = status
        payload["attrib"] = TaskEntity.model.attrib_model(
            startDate=start_date,
            endDate=end_date,
        )
        task = TaskEntity(
            project_name=self.project_name,
            payload=payload,
        )
        await task.save(conn)

        num_workfiles = random.randint(0, 5)
        for i in range(1, num_workfiles):
            fname = f"{self.project.code}_{folder.name}_{task.name}_v{i:03d}.ma"
            path = "{root[work]}/"
            path += "/".join(parents)
            path += f"/work/{task.name}/{fname}"

            workfile = WorkfileEntity(
                project_name=self.project_name,
                payload={
                    "path": path,
                    "task_id": task.id,
                    "created_by": "admin",
                    "tags": self.get_entity_tags(),
                    "status": self.get_entity_status(),
                },
            )
            await workfile.save(conn)
            self.workfile_count += 1

        return task

    async def create_representation(
        self,
        conn: Connection,
        folder: FolderEntity,
        product: ProductEntity,
        version: VersionEntity,
        **kwargs: Any,
    ) -> RepresentationEntity:
        self.representation_count += 1

        attrib = kwargs.get("attrib", {})
        if "template" in kwargs:
            attrib["template"] = kwargs["template"]
        kwargs["attrib"] = attrib

        #
        # Create a list of files
        #
        context = {
            "root": {"work": "{root[work]}"},
            "project": {"name": self.project_name},
            "path": "/".join(folder.parents + [folder.name]),  # type: ignore
            "product": {
                "type": product.product_type,
                "name": product.name,
            },
            "version": version.version,
            "folder": {"name": folder.name},
        }

        # Backwards compatibility
        template = (
            kwargs["attrib"]["template"]
            .replace("{folder}", "{folder[name]}")
            .replace("{project_name}", "{project[name]}")
            .replace("{product}", "{product[name]}")
            .replace("{product_type}", "{product[type]}")
            .replace("{root}", "{root[work]}")
        )
        files = []
        if "{frame}" in template:
            frame_start = folder.attrib.frameStart
            frame_end = folder.attrib.frameEnd
        else:
            frame_start = 0
            frame_end = 0
        for i in range(frame_start, frame_end + 1):
            fid = create_uuid()
            fpath = template.format(frame=f"{i:06d}", **context)
            files.append(
                {
                    "id": fid,
                    "path": fpath,
                    "size": random.randint(1_000_000, 10_000_000),
                    "hash": get_random_md5(),
                }
            )

        #
        # Save the representation
        #

        representation = RepresentationEntity(
            project_name=self.project_name,
            payload={
                "version_id": version.id,
                "tags": self.get_entity_tags(),
                "status": self.get_entity_status(),
                "files": files,
                "data": {
                    "context": context,
                },
                **kwargs,
            },
        )
        await representation.save(conn)

        return representation
