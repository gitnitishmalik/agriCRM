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
