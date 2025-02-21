__all__ = ["EntityID"]

import uuid
from typing import Any

from pydantic import Field

from .hashing import create_uuid


class EntityID:
    example: str = "af10c8f0e9b111e9b8f90242ac130003"
    META: dict[str, Any] = {
        "example": "af10c8f0e9b111e9b8f90242ac130003",
        "min_length": 32,
        "max_length": 32,
        "regex": r"^[0-9a-f]{32}$",
    }

    @classmethod
    def create(cls) -> str:
        return create_uuid()

    @classmethod
    def parse(
        cls, entity_id: str | uuid.UUID | None, allow_nulls: bool = False
    ) -> str | None:
        """Convert UUID object or its string representation to string"""
        if entity_id is None and allow_nulls:
            return None
        if isinstance(entity_id, uuid.UUID):
            return entity_id.hex
        if isinstance(entity_id, str):
            entity_id = entity_id.replace("-", "")
            if len(entity_id) == 32:
                return entity_id
        raise ValueError(f"Invalid entity ID {entity_id}")

    @classmethod
    def field(cls, name: str = "entity") -> Field:  # type: ignore
        return Field(  # type: ignore
            title=f"{name.capitalize()} ID",
            description=f"{name.capitalize()} ID",
            **cls.META,
        )
