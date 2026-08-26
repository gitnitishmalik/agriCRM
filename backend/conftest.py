"""
Test setup for a database Django does not own.

The business schema lives in `sql/schema.sql`, and every model that maps it is
`managed = False` — which means `migrate` creates none of those tables and a
test database built the ordinary way has no `ref`, `core`, `comm`, `crm`, `dq`
or `audit` in it at all.

So the DDL is applied to the test database directly, once per session, right
after pytest-django finishes creating it. This is not a workaround: it is the
same file CI applies and the same file production runs, which is the point.
A model that drifts from the DDL fails here rather than in staging.

`seed_reference.sql` follows, because states and crops are reference data, not
fixtures — a test that invents its own state id is testing something the
application will never see.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.db import connection

SQL_DIR = Path(__file__).resolve().parents[1] / "agri-crm-docs" / "sql"


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Apply the DDL-owned schema on top of the migrated test database."""
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        for name in ("schema.sql", "seed_reference.sql"):
            cursor.execute((SQL_DIR / name).read_text(encoding="utf-8"))
    return django_db_setup
