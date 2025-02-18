from datetime import datetime
from enum import IntEnum
from typing import Annotated, Any

from ayon_server.types import Field, OPModel, ProjectLevelEntityType
from ayon_server.utils import create_uuid


class ListAccessLevel(IntEnum):
    READ = 10  # Can view the list and its items
    UPDATE = 20  # Can update attributes of the list items
    CONSTRUCT = 30  # Can add/remove items from the list or materialize new items
    ADMIN = 40  # Can update/delete the list itself and add new users to the list


class EntityListItemModel(OPModel):
    id: Annotated[str, Field(default_factory=create_uuid)]

    entity_type: ProjectLevelEntityType
    entity_id: str = Field(...)
    position: Annotated[int, Field(0)]

    attrib: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime | None = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = Field(default_factory=datetime.utcnow)


class EntityListModel(OPModel):
    id: Annotated[str, Field(default_factory=create_uuid)]
    label: Annotated[str, Field(...)]
    list_type: Annotated[str, Field(...)]
    template: Annotated[dict[str, Any] | None, Field()] = None
    tags: list[str] = Field(default_factory=list)
    items: list[EntityListItemModel] = Field(default_factory=list)
    access: dict[str, ListAccessLevel] = Field(default_factory=dict)
