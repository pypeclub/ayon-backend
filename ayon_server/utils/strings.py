__all__ = [
    "camelize",
    "get_base_name",
    "get_nickname",
    "indent",
    "obscure",
    "parse_access_token",
    "parse_api_key",
    "slugify",
]

import functools
import os
import string
from typing import Literal, overload

import codenamize
import unidecode

SLUG_WHITELIST = string.ascii_letters + string.digits
SLUG_SEPARATORS = " ,./\\;:!|*^#@~+-_="


def camelize(src: str) -> str:
    """Convert snake_case to camelCase."""
    components = src.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def get_base_name(file_path: str) -> str:
    return os.path.splitext(os.path.basename(file_path))[0]


@functools.lru_cache(maxsize=128)
def get_nickname(text: str, length: int = 1):
    return codenamize.codenamize(text, length)


def indent(text: str, length: int = 4) -> str:
    """Indent a multi-line text."""
    return (
        "\n".join([f"{length*' '}{s.rstrip()}" for s in text.split("\n")])
        if text.endswith("\n")
        else ""
    )


@functools.lru_cache(maxsize=128)
def obscure(text: str):
    """obscure all characters in the text except spaces."""
    return "".join("*" if c != " " else c for c in text)


def parse_access_token(authorization: str) -> str | None:
    """Parse an authorization header value.

    Get a TOKEN from "Bearer TOKEN" and return a token
    string or None if the input value does not match
    the expected format (64 bytes string)
    """
    if (not authorization) or not isinstance(authorization, str):
        return None
    try:
        # ttype is not a ttypo :)
        ttype, token = authorization.split()
    except ValueError:
        return None
    if ttype.lower() != "bearer":
        return None
    if len(token) != 64:
        return None
    return token


def parse_api_key(authorization: str) -> str | None:
    if (not authorization) or not isinstance(authorization, str):
        return None
    try:
        ttype, token = authorization.split()
    except ValueError:
        return None
    if ttype.lower() != "apikey":
        return None
    return token


@overload
def slugify(
    input_string: str,
    *,
    separator: str = "-",
    lower: bool = True,
    make_set: Literal[False] = False,
    min_length: int = 1,
    slug_whitelist: str = SLUG_WHITELIST,
    split_chars: str = SLUG_SEPARATORS,
) -> str: ...


@overload
def slugify(
    input_string: str,
    *,
    separator: str = "-",
    lower: bool = True,
    make_set: Literal[True] = True,
    min_length: int = 1,
    slug_whitelist: str = SLUG_WHITELIST,
    split_chars: str = SLUG_SEPARATORS,
) -> set[str]: ...


def slugify(
    input_string: str,
    *,
    separator: str = "-",
    lower: bool = True,
    make_set: bool = False,
    min_length: int = 1,
    slug_whitelist: str = SLUG_WHITELIST,
    split_chars: str = SLUG_SEPARATORS,
) -> str | set[str]:
    """Slugify a text string.

    This function removes transliterates input string to ASCII,
    removes special characters and use join resulting elements
    using specified separator.

    Args:
        input_string (str):
            Input string to slugify

        separator (str):
            A string used to separate returned elements (default: "-")

        lower (bool):
            Convert to lower-case (default: True)

        make_set (bool):
            Return "set" object instead of string

        min_length (int):
            Minimal length of an element (word)

        slug_whitelist (str):
            Characters allowed in the output
            (default: ascii letters, digits and the separator)

        split_chars (str):
            Set of characters used for word splitting (there is a sane default)

    """
    input_string = unidecode.unidecode(input_string)
    if lower:
        input_string = input_string.lower()
    input_string = "".join(
        [ch if ch not in split_chars else " " for ch in input_string]
    )
    input_string = "".join(
        [ch if ch in slug_whitelist + " " else "" for ch in input_string]
    )
    elements = [
        elm.strip() for elm in input_string.split(" ") if len(elm.strip()) >= min_length
    ]
    return set(elements) if make_set else separator.join(elements)
