from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo, Undefined
from pydantic.typing import NoArgAnyCallable

from ayon_server.logging import logger
from ayon_server.utils import camelize, json_dumps, json_loads


class RestModel(BaseModel):
    """Base API model."""

    class Config:
        """API model config."""

        orm_mode = True
        allow_population_by_field_name = True
        alias_generator = camelize
        json_loads = json_loads
        json_dumps = json_dumps


def RestField(
    default: Any = Undefined,
    *,
    default_factory: NoArgAnyCallable | None = None,
    alias: str | None = None,
    title: str | None = None,
    description: str | None = None,
    gt: float | None = None,
    ge: float | None = None,
    lt: float | None = None,
    le: float | None = None,
    multiple_of: float | None = None,
    allow_inf_nan: bool | None = None,
    max_digits: int | None = None,
    decimal_places: int | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
    unique_items: bool | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    allow_mutation: bool = True,
    regex: str | None = None,
    discriminator: str | None = None,
    repr: bool = True,
    # AYON settings specifics
    example: Any = None,
    deprecated: bool = False,
    examples: list[Any] | None = None,
    # everything else
    **kwargs: Any,
) -> Any:
    # sanity checks

    if kwargs:
        logger.debug(f"RestField: unsupported argument: {kwargs}")

    # Pydantic 1 uses `example` while Pydantic 2 uses `examples`
    # We will support both, but before Pydantic 2 is used, `examples` will
    # just use the first example. No one provides multiple examples anyway.

    examples = examples or []
    if example is not None:
        examples.append(example)
    if not examples:
        examples = None

    # extras

    extra: dict[str, Any] = {}

    if examples and isinstance(examples, list):
        extra["example"] = examples[0]
        # in pydantic 2, use:
        # extra["examples"] = examples

    # construct FieldInfo

    field_info = FieldInfo(
        default,
        default_factory=default_factory,
        alias=alias,
        title=title,
        description=description,
        gt=gt,
        ge=ge,
        lt=lt,
        le=le,
        multiple_of=multiple_of,
        allow_inf_nan=allow_inf_nan,
        max_digits=max_digits,
        decimal_places=decimal_places,
        min_items=min_items,
        max_items=max_items,
        unique_items=unique_items,
        min_length=min_length,
        max_length=max_length,
        allow_mutation=allow_mutation,
        regex=regex,
        discriminator=discriminator,
        deprecated=deprecated,
        repr=repr,
        **extra,
    )

    field_info._validate()
    return field_info
