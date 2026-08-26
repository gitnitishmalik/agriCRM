"""
Mapping helpers for the DDL-owned schemas.

The business schema is owned by `sql/schema.sql`, not by Django models
(CLAUDE.md). Django manages only its own tables; `ref`, `core`, `comm`, `crm`,
`dq` and `audit` are applied by DDL and mapped with `managed = False` models.

Django has no `db_schema` option. The supported way to reach a non-public
schema is to close and reopen the quoting inside `db_table`, so that
`ref"."state` is emitted as `"ref"."state"`. It is easy to get subtly wrong by
hand and impossible to grep for afterwards, so it lives here once.
"""

from __future__ import annotations


def schema_table(schema: str, table: str) -> str:
    """Return a `db_table` value that resolves to `schema.table`."""
    return f'{schema}"."{table}'
