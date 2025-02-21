from datetime import datetime
from typing import TYPE_CHECKING, Any

import strawberry
from strawberry import LazyType

from ayon_server.entities import ProjectEntity
from ayon_server.graphql.connections import ActivitiesConnection
from ayon_server.graphql.nodes.common import ProductType, ThumbnailInfo
from ayon_server.graphql.resolvers.activities import get_activities
from ayon_server.graphql.resolvers.folders import get_folder, get_folders
from ayon_server.graphql.resolvers.products import get_product, get_products
from ayon_server.graphql.resolvers.representations import (
    get_representation,
    get_representations,
)
from ayon_server.graphql.resolvers.tasks import get_task, get_tasks
from ayon_server.graphql.resolvers.versions import get_version, get_versions
from ayon_server.graphql.resolvers.workfiles import get_workfile, get_workfiles
from ayon_server.graphql.utils import parse_attrib_data
from ayon_server.helpers.tags import get_used_project_tags
from ayon_server.lib.postgres import Postgres
from ayon_server.utils import json_dumps

if TYPE_CHECKING:
    from ayon_server.graphql.connections import (
        FoldersConnection,
        ProductsConnection,
        RepresentationsConnection,
        TasksConnection,
        VersionsConnection,
        WorkfilesConnection,
    )
    from ayon_server.graphql.nodes.folder import FolderNode
    from ayon_server.graphql.nodes.product import ProductNode
    from ayon_server.graphql.nodes.representation import RepresentationNode
    from ayon_server.graphql.nodes.task import TaskNode
    from ayon_server.graphql.nodes.version import VersionNode
    from ayon_server.graphql.nodes.workfile import WorkfileNode
else:
    FoldersConnection = LazyType["FoldersConnection", "..connections"]
    RepresentationsConnection = LazyType["RepresentationsConnection", "..connections"]
    ProductsConnection = LazyType["ProductsConnection", "..connections"]
    TasksConnection = LazyType["TasksConnection", "..connections"]
    VersionsConnection = LazyType["VersionsConnection", "..connections"]
    WorkfilesConnection = LazyType["WorkfilesConnection", "..connections"]

    FolderNode = LazyType["FolderNode", ".folder"]
    RepresentationNode = LazyType["RepresentationNode", ".representation"]
    ProductNode = LazyType["ProductNode", ".product"]
    TaskNode = LazyType["TaskNode", ".task"]
    VersionNode = LazyType["VersionNode", ".version"]
    WorkfileNode = LazyType["WorkfileNode", ".workfile"]


@strawberry.type
class TaskType:
    name: str


@strawberry.type
class FolderType:
    name: str

    @strawberry.field
    def icon(self) -> str:
        return self.name.lower()


@strawberry.type
class ProjectBundleType:
    production: str | None = None
    staging: str | None = None


@ProjectEntity.strawberry_attrib()
class ProjectAttribType:
    pass


@strawberry.type
class ProjectNode:
    name: str = strawberry.field()
    project_name: str = strawberry.field()
    code: str = strawberry.field()
    attrib: ProjectAttribType
    data: str | None
    active: bool
    library: bool
    thumbnail: ThumbnailInfo | None = None
    bundle: ProjectBundleType
    created_at: datetime
    updated_at: datetime

    folder: FolderNode | None = strawberry.field(
        resolver=get_folder,
        description=get_folder.__doc__,
    )

    folders: FoldersConnection = strawberry.field(
        resolver=get_folders,
        description=get_folders.__doc__,
    )

    task: TaskNode | None = strawberry.field(
        resolver=get_task,
        description=get_task.__doc__,
    )

    tasks: TasksConnection = strawberry.field(
        resolver=get_tasks,
        description=get_tasks.__doc__,
    )

    product: ProductNode | None = strawberry.field(
        resolver=get_product,
        description=get_product.__doc__,
    )

    products: ProductsConnection = strawberry.field(
        resolver=get_products,
        description=get_products.__doc__,
    )

    version: VersionNode | None = strawberry.field(
        resolver=get_version,
        description=get_version.__doc__,
    )

    versions: VersionsConnection = strawberry.field(
        resolver=get_versions,
        description=get_versions.__doc__,
    )

    representation: RepresentationNode | None = strawberry.field(
        resolver=get_representation,
        description=get_representation.__doc__,
    )

    representations: RepresentationsConnection = strawberry.field(
        resolver=get_representations,
        description=get_representations.__doc__,
    )

    workfile: WorkfileNode | None = strawberry.field(
        resolver=get_workfile,
        description=get_workfile.__doc__,
    )

    workfiles: WorkfilesConnection = strawberry.field(
        resolver=get_workfiles,
        description=get_workfiles.__doc__,
    )

    activities: ActivitiesConnection = strawberry.field(
        resolver=get_activities,
        description=get_activities.__doc__,
    )

    @strawberry.field(description="List of project's task types")
    async def task_types(self, active_only: bool = False) -> list[TaskType]:
        if active_only:
            query = f"""
                SELECT DISTINCT(task_type) AS task_type
                FROM project_{self.project_name}.tasks
            """
        else:
            query = f"""
                SELECT name AS task_type
                FROM project_{self.project_name}.task_types
                ORDER BY position
            """
        return [
            TaskType(name=row["task_type"]) async for row in Postgres.iterate(query)
        ]

    @strawberry.field(description="List of project's folder types")
    async def folder_types(self, active_only: bool = False) -> list[FolderType]:
        if active_only:
            query = f"""
                SELECT DISTINCT(folder_type) AS folder_type
                FROM project_{self.project_name}.folders
            """
        else:
            query = f"""
                SELECT name AS folder_type
                FROM project_{self.project_name}.folder_types
                ORDER BY position
            """
        return [
            FolderType(name=row["folder_type"]) async for row in Postgres.iterate(query)
        ]

    @strawberry.field(description="List of project's product types")
    async def product_types(self) -> list[ProductType]:
        return [
            ProductType(
                name=row["name"],
                icon=row["data"].get("icon"),
                color=row["data"].get("color"),
            )
            async for row in Postgres.iterate(
                f"""
                SELECT name, data FROM product_types
                WHERE name IN (
                    SELECT DISTINCT(product_type)
                    FROM project_{self.project_name}.products
                )
                ORDER BY name ASC
            """
            )
        ]

    @strawberry.field(description="List of tags used in the project")
    async def used_tags(self) -> list[str]:
        return await get_used_project_tags(self.project_name)


def project_from_record(
    project_name: str | None, record: dict[str, Any], context: dict[str, Any]
) -> ProjectNode:
    """Construct a project node from a DB row."""

    thumbnail = None

    data = record.get("data", {})
    bundle_data = data.get("bundle", {})
    if bundle_data:
        bundle = ProjectBundleType(
            production=bundle_data.get("production", None),
            staging=bundle_data.get("staging", None),
        )

    return ProjectNode(
        name=record["name"],
        code=record["code"],
        project_name=record["name"],
        active=record["active"],
        library=record["library"],
        attrib=parse_attrib_data(
            ProjectAttribType,
            record["attrib"],
            user=context["user"],
            project_name=record["name"],
        ),
        thumbnail=thumbnail,
        data=json_dumps(data) if data else None,
        bundle=bundle,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


ProjectNode.from_record = staticmethod(project_from_record)  # type: ignore
