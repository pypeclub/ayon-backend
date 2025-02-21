import re
from typing import Any, get_args

from ayon_server.lib.postgres import Postgres
from ayon_server.logging import logger

from .models import ActivityReferenceModel, EntityLinkTuple, ReferencedEntityType

MAX_BODY_LENGTH = 2000

# extract links, but not images
LINK_PATTERN = re.compile(r"(?<!\!)\[(.*?)\]\((.*?)\)")


def extract_link_tuples(md_text: str) -> list[EntityLinkTuple]:
    links: set[EntityLinkTuple] = set()
    for link in LINK_PATTERN.findall(md_text):
        try:
            entity_type, entity_id = link[1].split(":")
            if entity_type not in get_args(ReferencedEntityType):
                continue
            links.add((entity_type, entity_id))
        except ValueError:
            logger.debug("Invalid reference link format")
    return list(links)


def extract_mentions(
    md_text: str,
) -> set[ActivityReferenceModel]:
    """Extract entity and user mentions from markdown text.

    Mentions are in the format (label)[entity_type:entity_id],
    label is ignored.
    """

    references: set[ActivityReferenceModel] = set()

    for entity_type, entity_id in extract_link_tuples(md_text):
        if entity_type == "user":
            references.add(
                ActivityReferenceModel(
                    entity_name=entity_id,
                    reference_type="mention",
                    entity_type="user",
                    entity_id=None,
                )
            )
        else:
            references.add(
                ActivityReferenceModel(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    entity_name=None,
                    reference_type="mention",
                )
            )
    return references


def is_body_with_checklist(md_text: str) -> bool:
    """Check if the markdown text is a body with a checklist."""

    checkbox_pattern = re.compile(r"^\s*[\-\*]\s*\[[ xX]\]", re.MULTILINE)
    match = checkbox_pattern.search(md_text)
    return match is not None


async def process_activity_files(
    project_name: str,
    files: list[str],
) -> list[dict[str, Any]]:
    """Check if the files are valid and return their metadata.

    Args:
    files: list of file IDs
    """
    result = []

    query = f"""
        SELECT id, size, author, data, created_at, updated_at
        FROM project_{project_name}.files
        WHERE id = ANY($1)
    """

    async for row in Postgres.iterate(query, files):
        file_info = {
            "id": row["id"],
            "size": row["size"],
            "author": row["author"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        data = row["data"]

        if filename := data.get("filename"):
            file_info["filename"] = filename

        if mime := data.get("mime"):
            file_info["mime"] = mime

        result.append(file_info)

    return result
