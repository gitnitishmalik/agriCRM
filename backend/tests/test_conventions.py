"""
Project conventions that outlived the framework they were first written against.

These assertions began life in `test_django_parity.py`, comparing the FastAPI
service against Django while both ran. Django is gone; the properties are not,
because they were never Django's — they are this project's, and the DDL and
CLAUDE.md are their real source.

🔴 Restated against fixed expectations rather than deleted with the comparison.
A test that only ever said "the two services agree" proves nothing once there
is one service; a test that says "the total is 15,78,250.00" still does.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.anyio


async def test_money_is_grouped_the_indian_way():
    """
    🔴 `15,78,250.00`, not `1,578,250.00`.

    Lakh and crore, not thousands — CLAUDE.md's conventions section. The
    server formats every figure it sends precisely so this rule lives in one
    place; a second implementation in TypeScript would be a second one to get
    wrong, and the two would disagree by a comma on a document a customer
    reads.
    """
    from backend.money import format_inr

    assert format_inr(1578250) == "15,78,250.00"
    assert format_inr(25096770) == "2,50,96,770.00"
    assert format_inr(999) == "999.00"
    assert format_inr(Decimal("38055.00")) == "38,055.00"
    # The boundary where Indian grouping stops matching Western.
    assert format_inr(100000) == "1,00,000.00"


async def test_amounts_in_words_use_lakh_and_crore():
    """
    Generated, never typed. The historical sheet contains "ninteen" — a typo
    that cannot recur once the words are computed from the figure.
    """
    from backend.money import rupees_in_words

    words = rupees_in_words(Decimal("645519.00")).lower()
    assert "lakh" in words
    assert "million" not in words


async def test_the_invoice_status_vocabulary_is_the_ddl_s():
    """
    🔴 New enum values are appended, never renamed or removed (CLAUDE.md).

    The DDL owns `crm.invoice_status`; this list mirrors it, and a rename in
    either place without the other is a write that fails at runtime.
    """
    from backend.models.billing import INVOICE_STATUSES

    assert set(INVOICE_STATUSES) == {
        "draft",
        "issued",
        "on_hold",
        "part_paid",
        "paid",
        "cancelled",
        "discarded",
    }


async def test_the_status_list_matches_the_database_enum(session):
    """
    The mirror checked against the real thing, rather than against a copy of
    itself. This is the assertion that catches a value added to the DDL and
    not to the model.
    """
    from sqlalchemy import text

    from backend.models.billing import INVOICE_STATUSES

    rows = await session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'crm' AND t.typname = 'invoice_status'"
        )
    )
    in_database = {row[0] for row in rows}
    assert in_database == set(INVOICE_STATUSES), (
        f"`crm.invoice_status` and `INVOICE_STATUSES` disagree. "
        f"Only in the database: {sorted(in_database - set(INVOICE_STATUSES))}; "
        f"only in the model: {sorted(set(INVOICE_STATUSES) - in_database)}"
    )


async def test_mfa_is_mandatory_for_the_privileged_roles():
    """
    🔴 Doc 12 §1. These four require a second factor; the rest do not. A role
    quietly dropped from this set is a privileged account behind a password.
    """
    from backend.models.accounts import MFA_REQUIRED_ROLES, ROLES

    assert MFA_REQUIRED_ROLES == {
        "data_ops",
        "campaign_manager",
        "compliance",
        "admin",
    }
    assert MFA_REQUIRED_ROLES <= set(ROLES)
    assert "field_agent" not in MFA_REQUIRED_ROLES


async def test_cross_territory_roles_are_the_documented_four():
    """Roles exempt from territory scoping in the RLS policy (Doc 12 §1)."""
    from backend.models.accounts import CROSS_TERRITORY_ROLES, ROLES

    assert CROSS_TERRITORY_ROLES == {"data_ops", "compliance", "admin", "leadership"}
    assert CROSS_TERRITORY_ROLES <= set(ROLES)


async def test_area_converts_to_hectares_exactly():
    """
    🔴 CLAUDE.md: all area in hectares. 1 acre = 0.40468564224 ha exactly, by
    international definition — not an approximation, which is why the DDL
    computes it as a generated column rather than trusting a caller.
    """
    from backend.money import to_hectares

    # INVOICE.md I-1's own exit gate: 100 acres stores as 40.4686 ha.
    assert to_hectares(Decimal(100), "acre") == Decimal("40.4686")
    assert to_hectares(Decimal("65.7"), "sq_km") == Decimal("6570.0000")
    assert to_hectares(Decimal(5), "hectare") == Decimal("5.0000")
    # A unit with no area meaning converts to nothing, rather than to zero.
    assert to_hectares(Decimal(3), "each") is None


async def test_the_app_imports_from_inside_the_backend_directory():
    """
    🔴 `uvicorn main:app` run from inside `backend/` must work.

    Everything in `main.py` imports `backend.*` absolutely, which needs the
    *parent* of `backend/` on `sys.path`. From the repository root that is
    already true. From inside `backend/` — the obvious thing to type when you
    are sitting in the directory — `sys.path[0]` is `backend/` itself and the
    first absolute import dies with `ModuleNotFoundError: No module named
    'backend'`.

    The traceback names the failing import and not the working directory, so it
    reads like a broken installation rather than a `cd`. It was reported twice.
    `main.py` puts the repository root on the path when `__package__` is empty;
    this asserts that guard still works, in a subprocess because the
    alternative is mutating `sys.path` inside the suite.
    """
    import subprocess
    import sys
    from pathlib import Path

    import anyio

    backend_dir = Path(__file__).resolve().parents[1]

    # In a worker thread rather than `asyncio.create_subprocess_exec`. On
    # Windows `backend/__init__.py` installs the selector event-loop policy so
    # psycopg can run async at all, and asyncio's subprocess support requires
    # the proactor loop it replaces — so the async spelling deadlocks on the
    # exact platform this test exists for.
    def _probe() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", "import main; assert main.app is not None; print('ok')"],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    result = await anyio.to_thread.run_sync(_probe)

    assert result.returncode == 0, (
        "`uvicorn main:app` from inside backend/ is broken again. The guard at "
        "the top of backend/main.py is what keeps it working. " + result.stderr[-2000:]
    )
