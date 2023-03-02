import datetime
from typing import Annotated

from nxtools import slugify
from strawberry.types import Info

from ayon_server.graphql.connections import EventsConnection
from ayon_server.graphql.edges import EventEdge
from ayon_server.graphql.nodes.event import EventNode
from ayon_server.graphql.resolvers.common import (
    ARGAfter,
    ARGBefore,
    ARGFirst,
    ARGLast,
    FieldInfo,
    argdesc,
    create_pagination,
    resolve,
)
from ayon_server.types import (
    validate_name_list,
    validate_topic_list,
    validate_user_name_list,
)
from ayon_server.utils import SQLTool


async def get_events(
    root,
    info: Info,
    topics: Annotated[list[str] | None, argdesc("List of topics")] = None,
    projects: Annotated[list[str] | None, argdesc("List of projects")] = None,
    users: Annotated[list[str] | None, argdesc("List of users")] = None,
    states: Annotated[list[str] | None, argdesc("List of states")] = None,
    older_than: Annotated[str | None, argdesc("Timestamp")] = None,
    newer_than: Annotated[str | None, argdesc("Timestamp")] = None,
    filter: str | None = None,
    includeLogs: Annotated[bool, argdesc("Include logs in the response")] = False,
    first: ARGFirst = None,
    after: ARGAfter = None,
    last: ARGLast = None,
    before: ARGBefore = None,
) -> EventsConnection:
    """Return a list of events."""

    sql_conditions = []

    if topics:
        topics = validate_topic_list(topics)
        sql_conditions.append(
            f"topic LIKE ANY(array[{SQLTool.array(topics, nobraces=True)}])"
        )
    elif not includeLogs:
        sql_conditions.append("NOT topic LIKE 'log.%'")

    if projects:
        projects = validate_name_list(projects)
        sql_conditions.append(f"project_name IN {SQLTool.array(projects)}")
    if users:
        users = validate_user_name_list(users)
        sql_conditions.append(f"user_name IN {SQLTool.array(users)}")
    if states:
        states = validate_name_list(states)
        sql_conditions.append(f"status IN {SQLTool.array(states)}")

    if older_than:
        _ = datetime.datetime.fromisoformat(older_than)
        sql_conditions.append(f"created_at < '{older_than}'")

    if newer_than:
        _ = datetime.datetime.fromisoformat(newer_than)
        sql_conditions.append(f"created_at > '{newer_than}'")

    if filter:
        elms = slugify(filter, make_set=True)
        search_cols = ["topic", "project_name", "user_name", "description"]
        lconds = []
        for elm in elms:
            if len(elm) < 3:
                continue

            lconds.append(
                f"""({' OR '.join([f"{col} LIKE '%{elm}%'" for col in search_cols])})"""
            )

        if lconds:
            sql_conditions.extend(lconds)

    paging_fields = FieldInfo(info, ["events"])
    need_cursor = paging_fields.has_any(
        "events.pageInfo.startCursor",
        "events.pageInfo.endCursor",
        "events.edges.cursor",
    )

    order_by = ["creation_order"]
    pagination, paging_conds, cursor = create_pagination(
        order_by,
        first,
        after,
        last,
        before,
        need_cursor=need_cursor,
    )
    sql_conditions.extend(paging_conds)

    query = f"""
        SELECT {cursor}, * FROM events
        {SQLTool.conditions(sql_conditions)}
        {pagination}
    """

    return await resolve(
        EventsConnection,
        EventEdge,
        EventNode,
        None,
        query,
        first,
        last,
        context=info.context,
    )
