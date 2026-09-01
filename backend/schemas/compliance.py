"""Tax-code knowledge and export shapes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Citation(BaseModel):
    title: str
    url: str | None
    reviewer: str | None
    reviewed_at: str | None


class TaxCodeOut(BaseModel):
    id: str
    code: str
    code_kind: str
    description: str
    gst_rate_pct: str | None
    jurisdiction: str
    effective_from: str
    effective_to: str | None
    review_status: str
    #: 🔴 True only when a named CA approved it. Computed server-side so a
    #: client cannot arrive at it by testing `review_status != "rejected"`,
    #: which would present an unreviewed AI suggestion as a classification.
    is_verified: bool
    citation: Citation
    keywords: list[str]
    notes: str | None
    label: str


class TaxCodeSuggestion(TaxCodeOut):
    """The same record, returned as a suggestion against a line description."""


class ApproveRequest(BaseModel):
    """
    🔴 The reviewer's name is required. "Approved" with nobody's name against
    it is not a review, and the database refuses the row without one.
    """

    reviewer_name: str = Field(min_length=2, max_length=200)


class Gstr1Summary(BaseModel):
    invoice_count: int
    b2b_count: int
    b2c_count: int
    taxable_value: str
    tax_amount: str
    total_value: str
    display: dict[str, str]


class Gstr1WorkingPaper(BaseModel):
    period: dict[str, str]
    entity_code: str | None
    b2b: list[dict[str, Any]]
    b2c: list[dict[str, Any]]
    summary: Gstr1Summary
    numbers_in_period: list[str]
    #: The valuable half: the rows a portal would reject, found here instead.
    warnings: list[dict[str, Any]]
    blocking_warnings: list[dict[str, Any]]
    disclaimer: str
    #: 🔴 In the payload, not in a doc. The moment somebody is about to upload
    #: a file to a portal is the moment they are not reading documentation.
    not_a_filing: str
