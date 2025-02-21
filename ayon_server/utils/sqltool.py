__all__ = ["SQLTool"]

import uuid
from typing import Any

from .entity_id import EntityID


class SQLTool:
    """SQL query construction helpers."""

    @staticmethod
    def array(elements: list[str] | list[int], curly=False, nobraces=False) -> str:
        """Return a SQL-friendly list string."""
        if nobraces:
            start = end = ""
        else:
            start = "'{" if curly else "("
            end = "}'" if curly else ")"
        return (
            start
            + (
                ", ".join(
                    [
                        (f"'{e}'" if isinstance(e, str) and not curly else str(e))
                        for e in elements
                    ]
                )
            )
            + end
        )

    @staticmethod
    def id_array(ids: list[str] | list[uuid.UUID]) -> str:
        """Return a SQL-friendly list of IDs.

        list(['a', 'b', 'c']) becomes str("('a', 'b', 'c')")
        Also provided list elements must be valid entity IDs

        Null values will be ignored.
        """
        parsed = [EntityID.parse(id, allow_nulls=True) for id in ids]
        return "(" + (", ".join([f"'{id}'" for id in parsed if id is not None])) + ")"

    @staticmethod
    def conditions(
        condition_list: list[str],
        operand: str | None = "AND",
        add_where: bool | None = True,
    ) -> str:
        """Return a SQL-friendly list of conditions.

        list(['a = 1', 'b = 2']) becomes str("a = 1 AND b = 2")
        """
        if condition_list:
            return ("WHERE " if add_where else "") + (
                f" {operand} ".join(condition_list)
            )
        return ""

    @staticmethod
    def order(
        order: str | None = None,
        desc: bool | None = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        result = []
        if order:
            result.append(f"ORDER BY {order}")
            if desc:
                result.append("DESC")
            if limit:
                result.append(f"LIMIT {limit}")
                if offset:
                    result.append(f"OFFSET {offset}")
        if result:
            return " ".join(result)
        return ""

    @staticmethod
    def insert(table: str, **kwargs: Any) -> list[Any]:
        """Return an SQL INSERT statement.

        The result format is a list, in which the first element
        is the SQL statement and the other items are the values,
        so the results could be used as:.
            Postgres.execute(*SQLTool.insert(...))
        """

        keys = list(kwargs.keys())
        command = f"""
            INSERT INTO {table}
            ({', '.join(keys)})
            VALUES
            ({ ', '.join([f'${i+1}' for i, _ in enumerate(keys)]) })
            """
        result: list[Any] = [command]
        for key in keys:
            result.append(kwargs[key])
        return result

    @staticmethod
    def update(
        table: str,
        conditions: str,
        **kwargs: Any,
    ) -> list[Any]:
        """Return an SQL UPDATE statement.

        Updated fields shall be specified in kwargs.
        Conditions argument is a list of conditions provided as a string
        including WHERE statement (so it is compatible with SQLTool.conditions
        function without additional arguments.)

        Result is provided as an array with the SQL command as the first
        argument and followed by the values, so it is ready to be used in
        the Postgres.execute function when unpacked using a star operator.
        """

        keys = list(kwargs.keys())
        command = f"""
            UPDATE {table}
            SET {', '.join([f'{key} = ${i+1}' for i, key in enumerate(keys)])}
            {conditions}
            """

        result = [command]
        for key in keys:
            result.append(kwargs[key])
        return result
