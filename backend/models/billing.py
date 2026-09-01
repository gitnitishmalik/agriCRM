"""
Billing — `crm.billing_entity`, `crm.invoice`, and their children.

🔴 Several columns here are written by database triggers, not by the
application: `taxable_value`, `tax_amount` and `total_value` on the header are
rolled up from the lines, `quantity_ha` on a line is a generated column, and
`invoice_no` is allocated by a trigger at issue and is immutable afterwards
(smoke test 18).

They are mapped so they can be read, and every one of them is commented as
read-only. Writing to them from here would not raise — it would silently
disagree with the database until the next trigger fired and quietly won, which
is the worst shape a money bug can take.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    Boolean,
    Computed,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base
from backend.models.types import BILLING_UNIT, INVOICE_STATUS, TAX_TREATMENT

#: `crm.invoice_status`. Appended to, never renamed or removed (CLAUDE.md).
INVOICE_STATUSES = (
    "draft",
    "issued",
    "on_hold",
    "part_paid",
    "paid",
    "cancelled",
    "discarded",
)

#: Statuses that owe money and therefore count toward receivables.
OUTSTANDING_STATUSES = ("issued", "part_paid")

#: Excluded from every total. A cancelled invoice is a document that exists and
#: is not owed; counting it makes a receivables figure meaningless.
NOT_OWED_STATUSES = ("cancelled", "discarded", "draft")


class BillingEntity(Base):
    """
    The companies that issue invoices.

    🔴 Versioned by `valid_from`/`valid_to`, not edited in place. The bank
    details on a 2025 invoice must stay what they were, so changing an address
    or a signatory means closing the current row and opening a new one — and
    an old invoice still re-renders with the details it was issued under.
    """

    __tablename__ = "billing_entity"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(Text)
    legal_name: Mapped[str] = mapped_column(Text)

    # 🔴 Every field below is printed on the document, and each was missing
    # from this mapping while the templates referenced it.
    #
    # Jinja renders an undefined name as an empty string rather than raising,
    # so the invoice came out *looking* fine — with no issuer address, no bank
    # branch, no signatory title, no declaration and no jurisdiction note. It
    # rendered, it was 7,600 characters long, and it was not a tax invoice.
    #
    # That is the failure the seed command's own docstring warns about: a
    # document your customer will act on, missing the parts that make it one.
    # A mapping that omits a column the template prints is not a smaller
    # mapping, it is a silently wrong document.
    address_lines: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    state_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    gstin: Mapped[str | None] = mapped_column(Text, nullable=True)
    pan: Mapped[str | None] = mapped_column(Text, nullable=True)

    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_account_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_ifsc: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_branch: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    signatory_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    signatory_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    declaration: Mapped[str | None] = mapped_column(Text, nullable=True)
    jurisdiction_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    template_code: Mapped[str] = mapped_column(Text)
    logo_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # 🔴 Allocated by a trigger at issue, and immutable afterwards. Null on a
    # draft, which is why the register shows "— draft —" rather than a blank.
    invoice_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    financial_year: Mapped[str | None] = mapped_column(Text, nullable=True)

    billing_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm.billing_entity.id"), name="billing_entity_id"
    )
    entity_code: Mapped[str] = mapped_column(Text)
    # Which of T1/T2/T3 this document renders as (INVOICE.md §2.4).
    # Copied from the entity at creation and then frozen: re-rendering an
    # old invoice must reproduce the document that was actually sent.
    template_code: Mapped[str] = mapped_column(Text, server_default=FetchedValue())
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    buyer_name: Mapped[str] = mapped_column(Text)
    buyer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_gstin: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer_state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    buyer_is_govt_uin: Mapped[bool] = mapped_column(Boolean, default=False)

    buyer_order_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_order_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    letter_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)

    # T3 only — the Mizoram survey document prints a ship-to block.
    consignee_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    consignee_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    consignee_gstin: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_link_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    place_of_supply_state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    tax_treatment: Mapped[str] = mapped_column(TAX_TREATMENT, default="igst")
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("18.00"))

    # 🔴 Read-only. Rolled up from the lines by a trigger (smoke test 17).
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal(0))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal(0))
    total_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal(0))
    amount_in_words: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(INVOICE_STATUS, default="draft")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 🔴 The rendered document, addressed by hash. `pdf_sha256` is what proves
    # the PDF a customer holds is the PDF that was sent; `pdf_object_id`
    # points at the bytes in the object store (INVOICE.md §6.3).
    pdf_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_sha256: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pdf_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pdf_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.stored_object.id"), nullable=True
    )

    # A row imported from the FY24–FY26 history. Re-rendering one must
    # reproduce the original document rather than today's template, so the
    # flag is read by the renderer and by the pre-issue checks.
    is_historical: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 🔴 NOT NULL DEFAULT now() in the DDL. Django's `auto_now` set it
    # invisibly; SQLAlchemy does not, so it is set explicitly on every
    # write. Mapped non-nullable so the mismatch is a type error here
    # rather than an IntegrityError from the database at insert time.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    billing_entity: Mapped[BillingEntity] = relationship(lazy="joined")
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", lazy="selectin", order_by="InvoiceLine.line_no"
    )
    payments: Mapped[list[InvoicePayment]] = relationship(back_populates="invoice", lazy="selectin")

    @property
    def amount_received(self) -> Decimal:
        return sum((payment.amount for payment in self.payments), Decimal(0))

    @property
    def amount_outstanding(self) -> Decimal:
        """
        What is still owed.

        Zero for anything cancelled, discarded or still a draft — those are
        documents that exist and are not owed, and including them would make a
        receivables total meaningless.
        """
        if self.status in NOT_OWED_STATUSES:
            return Decimal(0)
        return self.total_value - self.amount_received


class InvoiceLine(Base):
    __tablename__ = "invoice_line"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    line_no: Mapped[int] = mapped_column(Integer)

    description: Mapped[str] = mapped_column(Text)
    hsn_sac: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    unit: Mapped[str] = mapped_column(BILLING_UNIT)

    # 🔴 GENERATED ALWAYS. CLAUDE.md: all area in hectares; acres and square
    # kilometres are input conveniences converted at the edge, and this column
    # *is* the conversion (smoke test 16).
    #
    # Declared `Computed` so SQLAlchemy leaves it out of INSERT and UPDATE
    # entirely. A comment saying "never write to it" is not a mechanism —
    # Postgres rejects the insert with `cannot insert a non-DEFAULT value into
    # column "quantity_ha"`, which is how this was found. The expression is
    # the DDL's, restated here only so the mapping is self-describing; this
    # package never emits DDL.
    quantity_ha: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4),
        Computed(
            "CASE unit "
            "WHEN 'acre' THEN quantity * 0.40468564224 "
            "WHEN 'sq_km' THEN quantity * 100 "
            "WHEN 'hectare' THEN quantity "
            "ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )

    rate: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    rate_is_tax_inclusive: Mapped[bool] = mapped_column(Boolean, default=False)

    # 🔴 Read-only. Computed server-side; see `apps/billing/backend.py::_write_lines`.
    line_taxable_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    location_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class InvoicePayment(Base):
    __tablename__ = "invoice_payment"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm.invoice.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    received_on: Mapped[date] = mapped_column(Date)
    # `mode`, not `method` — rtgs / neft / cheque / upi. Matching the DDL
    # rather than the nicer name, because the column is what exists.
    mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    invoice: Mapped[Invoice] = relationship(back_populates="payments")
