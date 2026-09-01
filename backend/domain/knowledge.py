"""
Effective-dated tax knowledge — HSN/SAC codes and their rates.

🔴 **Retrieval is by the invoice's date, never today's.** A rate that changed in
July does not retroactively apply to a June document, and a table without
effective dates cannot express that at all. Every lookup here takes a date and
uses it.

🔴 **Only an approved record may be presented as verified.** An `ai_suggested`
row may be shown, clearly labelled as unreviewed; presenting it as a
classification would be this codebase making a tax determination, which
INVOICE.md §9 puts outside the boundary.

🔴 **Nothing here scrapes or invents statutory data.** The seed set below is a
handful of records for the two service lines this business actually bills, each
carrying its notification number so the CA can check it. Production ingestion
is a documented, reviewed process — see `docs` in `seed_records()`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.scoping import EntityScope
from backend.models.invoice_ops import TaxCodeKnowledge

#: The two SACs this business bills under (INVOICE.md §2.2), with the
#: notification that set the rate. 🔴 Seeded as `under_review`, not
#: `approved` — a record nobody has checked is not verified merely because a
#: developer typed it, and the CHECK constraint refuses `approved` without a
#: named reviewer anyway.
SEED_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "code": "998611",
        "code_kind": "sac",
        "description": "Support services to agriculture — crop production",
        "gst_rate_pct": "18.00",
        "effective_from": "2017-07-01",
        "source_title": (
            "Notification No. 11/2017-Central Tax (Rate), Heading 9986 — "
            "support services to agriculture, hunting, forestry, fishing"
        ),
        "source_url": "https://cbic-gst.gov.in/",
        "keywords": ["spray", "spraying", "drone spray", "pesticide", "crop protection"],
        "notes": (
            "Drone spraying is billed under this SAC on every historical invoice. "
            "🔴 Whether some support services to agriculture are exempt rather "
            "than taxable at 18% is exactly the question INVOICE.md §5.4 puts to "
            "the CA — this record states the rate observed on the documents, not a "
            "determination."
        ),
    },
    {
        "code": "997319",
        "code_kind": "sac",
        "description": "Leasing or rental services concerning other machinery and equipment",
        "gst_rate_pct": "18.00",
        "effective_from": "2017-07-01",
        "source_title": "Notification No. 11/2017-Central Tax (Rate), Heading 9973",
        "source_url": "https://cbic-gst.gov.in/",
        "keywords": ["survey", "base map", "base-map", "mapping", "orthomosaic", "drone survey"],
        "notes": (
            "The Mizoram survey work is billed under this SAC. 🔴 Its rate is "
            "quoted GST-inclusive on those invoices (§2.2), which is a line-level "
            "flag rather than a different rate."
        ),
    },
)


class KnowledgeError(HTTPException):
    def __init__(self, detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(code, detail)


def serialise(row: TaxCodeKnowledge) -> dict[str, Any]:
    """
    One record, with its citation and its review state.

    🔴 `is_verified` is computed here rather than left to a client, because a
    UI deriving it from `review_status != "rejected"` would present an
    unreviewed AI suggestion as a classification.
    """
    return {
        "id": str(row.id),
        "code": row.code,
        "code_kind": row.code_kind,
        "description": row.description,
        "gst_rate_pct": str(row.gst_rate_pct) if row.gst_rate_pct is not None else None,
        "jurisdiction": row.jurisdiction,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": row.effective_to.isoformat() if row.effective_to else None,
        "review_status": row.review_status,
        "is_verified": row.review_status == "approved",
        "citation": {
            "title": row.source_title,
            "url": row.source_url,
            "reviewer": row.reviewer_name,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        },
        "keywords": list(row.keywords),
        "notes": row.notes,
        "label": (
            f"{row.code} — {row.description}, effective "
            f"{row.effective_from.isoformat()}"
            + ("" if row.review_status == "approved" else " (not reviewed by a CA)")
        ),
    }


async def effective_on(
    session: AsyncSession, *, code: str, on_date: date
) -> TaxCodeKnowledge | None:
    """
    The record for one code, valid on a given date.

    🔴 `on_date` is the *invoice's* date. Calling this with `date.today()` for
    a back-dated invoice is the bug this signature exists to make obvious.
    """
    return await session.scalar(
        select(TaxCodeKnowledge)
        .where(
            TaxCodeKnowledge.code == code,
            TaxCodeKnowledge.effective_from <= on_date,
            or_(
                TaxCodeKnowledge.effective_to.is_(None),
                TaxCodeKnowledge.effective_to >= on_date,
            ),
        )
        .order_by(TaxCodeKnowledge.effective_from.desc())
        .limit(1)
    )


async def search(
    session: AsyncSession,
    *,
    query: str | None = None,
    on_date: date | None = None,
    approved_only: bool = False,
) -> list[TaxCodeKnowledge]:
    """Records matching a description or keyword, valid on a date."""
    on_date = on_date or datetime.now(UTC).date()

    conditions = [
        TaxCodeKnowledge.effective_from <= on_date,
        or_(
            TaxCodeKnowledge.effective_to.is_(None),
            TaxCodeKnowledge.effective_to >= on_date,
        ),
    ]
    if approved_only:
        conditions.append(TaxCodeKnowledge.review_status == "approved")

    rows = list(
        await session.scalars(
            select(TaxCodeKnowledge)
            .where(*conditions)
            .order_by(TaxCodeKnowledge.code, TaxCodeKnowledge.effective_from.desc())
        )
    )

    if not query:
        return rows

    needle = query.strip().lower()
    return [
        row
        for row in rows
        if needle in row.code
        or needle in row.description.lower()
        or any(needle in keyword.lower() for keyword in row.keywords)
    ]


async def suggest(
    session: AsyncSession, *, description: str, on_date: date
) -> dict[str, Any] | None:
    """
    A code suggestion for a line description, valid on the invoice's date.

    Returns the record with its citation and review state, or None. Never a
    bare code — a code with no effective date and no source is an opinion.
    """
    text = (description or "").strip().lower()
    if not text:
        return None

    for row in await search(session, on_date=on_date):
        haystack = [row.description.lower(), *(k.lower() for k in row.keywords)]
        if any(term and term in text for term in haystack):
            return serialise(row)
    return None


async def approve(
    session: AsyncSession,
    scope: EntityScope,
    record_id: uuid.UUID,
    *,
    reviewer_name: str,
) -> TaxCodeKnowledge:
    """
    🔴 Mark a record as CA-reviewed. The only route to `is_verified`.

    Permission is checked at the route (`KNOWLEDGE_APPROVE`), and the database
    refuses `approved` without a named reviewer — so an approval cannot exist
    without somebody's name on it.
    """
    row = await session.scalar(select(TaxCodeKnowledge).where(TaxCodeKnowledge.id == record_id))
    if row is None:
        raise KnowledgeError("No such knowledge record.", status.HTTP_404_NOT_FOUND)

    if not reviewer_name.strip():
        raise KnowledgeError(
            "An approval needs the reviewer's name. 'Approved' with nobody's name "
            "against it is not a review."
        )

    row.review_status = "approved"
    row.reviewed_by = scope.user_id
    row.reviewer_name = reviewer_name.strip()
    row.reviewed_at = datetime.now(UTC)
    await session.flush()
    return row


async def seed_records(session: AsyncSession, *, created_by: uuid.UUID | None = None) -> int:
    """
    Insert the two SACs this business bills under, if they are absent.

    🔴 Seeded `under_review`. Production ingestion of the wider rate schedule
    is a documented process and not this function: a CBIC notification is a
    PDF, the rate changes by notification rather than on a schedule, and a
    scraped rate presented as verified would be this codebase making a tax
    determination. See DEPLOYMENT.md.
    """
    inserted = 0
    for record in SEED_RECORDS:
        existing = await session.scalar(
            select(TaxCodeKnowledge).where(
                TaxCodeKnowledge.code == record["code"],
                TaxCodeKnowledge.effective_from == date.fromisoformat(record["effective_from"]),
            )
        )
        if existing is not None:
            continue

        from decimal import Decimal

        session.add(
            TaxCodeKnowledge(
                code=record["code"],
                code_kind=record["code_kind"],
                description=record["description"],
                gst_rate_pct=Decimal(record["gst_rate_pct"]),
                jurisdiction="IN",
                effective_from=date.fromisoformat(record["effective_from"]),
                effective_to=None,
                source_title=record["source_title"],
                source_url=record.get("source_url"),
                review_status="under_review",
                keywords=record["keywords"],
                notes=record.get("notes"),
                created_at=datetime.now(UTC),
                created_by=created_by,
            )
        )
        inserted += 1

    if inserted:
        await session.flush()
    return inserted
