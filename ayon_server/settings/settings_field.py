from typing import Any

from pydantic.fields import FieldInfo, Undefined
from pydantic.typing import AnyCallable, NoArgAnyCallable

from ayon_server.logging import logger

"""
Unused pydantic fields
    exclude: Optional[Union['AbstractSetIntStr', 'MappingIntStrAny', Any]] = None,
    include: Optional[Union['AbstractSetIntStr', 'MappingIntStrAny', Any]] = None,
    const: Optional[bool] = None,
"""


def SettingsField(
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
    enum_resolver: AnyCallable | None = None,
    required_items: list[str] | None = None,
    section: str | None = None,
    widget: str | None = None,
    layout: str | None = None,
    tags: list[str] | None = None,
    scope: list[str] | None = None,
    placeholder: str | None = None,
    conditional_enum: bool = False,
    disabled: bool = False,
    # compatibility
    conditionalEnum: bool = False,  # backward compatibility
    examples: list[Any] | None = None,
    # everything else
    **kwargs: Any,
) -> Any:
    # sanity checks

    # conditionalEnum (camelCase) is deprecated, but used heavily.
    # We will need to support it for a long time, but it won't hurt.
    conditional_enum = conditional_enum or conditionalEnum

    if kwargs:
        logger.debug(f"SettingsField: unsupported argument: {kwargs}")

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
    if enum_resolver is not None:
        extra["enum_resolver"] = enum_resolver
    if required_items is not None:
        extra["required_items"] = required_items
    if section is not None:
        extra["section"] = section
    if widget is not None:
        extra["widget"] = widget
    if layout is not None:
        extra["layout"] = layout
    if tags is not None:
        extra["tags"] = tags
    if placeholder is not None:
        extra["placeholder"] = placeholder
    if conditional_enum:
        extra["conditional_enum"] = conditional_enum
    if scope is not None:
        extra["scope"] = scope
    if disabled is not None:
        extra["disabled"] = disabled

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
        repr=repr,
        **extra,
    )

    field_info._validate()
    return field_info
