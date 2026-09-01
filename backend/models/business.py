"""
The DDL-owned business schemas: `ref`, `core`, `dq`.

Every table here lives in `agri-crm-docs/sql/schema.sql`. These classes map
them and nothing more — no `create_all`, no migrations, no opinions about
shape. A column renamed in the DDL is a runtime error here exactly as it was
under Django, and the test suite applies the real schema so that error lands
in a test run rather than in production.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base
from backend.models.types import (
    CONTACT_KIND,
    FARMER_CLASS,
    GENDER,
    LEGAL_FORM,
    ORG_STATUS,
    ORG_TYPE,
    QUALITY_TIER,
    ROLE_TYPE,
    SOURCE_KIND,
    VERIFICATION_STATE,
)

# ---------------------------------------------------------------------------
# ref — geography
# ---------------------------------------------------------------------------


class State(Base):
    __tablename__ = "state"
    __table_args__ = {"schema": "ref"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lgd_code: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(Text)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    iso_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class District(Base):
    __tablename__ = "district"
    __table_args__ = {"schema": "ref"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lgd_code: Mapped[int] = mapped_column(Integer, unique=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("ref.state.id"))
    name: Mapped[str] = mapped_column(Text)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)

    state: Mapped[State] = relationship(lazy="joined")


class Block(Base):
    __tablename__ = "block"
    __table_args__ = {"schema": "ref"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lgd_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("ref.district.id"))
    name: Mapped[str] = mapped_column(Text)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)


class Village(Base):
    """
    🔴 ~660k rows once the LGD load lands. Never listed unscoped — see the
    guard in `api/routers/geography.py`.
    """

    __tablename__ = "village"
    __table_args__ = {"schema": "ref"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lgd_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_id: Mapped[int | None] = mapped_column(ForeignKey("ref.block.id"), nullable=True)
    district_id: Mapped[int | None] = mapped_column(ForeignKey("ref.district.id"), nullable=True)
    name: Mapped[str] = mapped_column(Text)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)


class Crop(Base):
    __tablename__ = "crop"
    __table_args__ = {"schema": "ref"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_season: Mapped[str | None] = mapped_column(String(16), nullable=True)


# ---------------------------------------------------------------------------
# core — organisations
# ---------------------------------------------------------------------------


class Organisation(Base):
    """
    🔴 One table with an `org_type` discriminator, not three.

    FPOs, sugar mills and cooperative societies share this row and extend it
    through profile tables. Three separate tables would triple every join,
    search and permission rule — a decision baked into the DDL that CLAUDE.md
    marks as "do not undo".
    """

    __tablename__ = "organisation"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_code: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    type: Mapped[str] = mapped_column(ORG_TYPE)
    status: Mapped[str] = mapped_column(ORG_STATUS, default="prospect")
    legal_form: Mapped[str] = mapped_column(LEGAL_FORM, default="unknown")

    name: Mapped[str] = mapped_column(Text)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)

    cin: Mapped[str | None] = mapped_column(String(21), nullable=True)
    registration_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)

    state_id: Mapped[int | None] = mapped_column(ForeignKey("ref.state.id"), nullable=True)
    district_id: Mapped[int | None] = mapped_column(ForeignKey("ref.district.id"), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_line2: Mapped[str | None] = mapped_column(Text, nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)

    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    established_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Billing contact and policy, added by `sql/schema_invoice_advanced.sql`.
    #
    # 🔴 On the organisation, not on the invoice. An invoice's buyer block is a
    # snapshot of what was printed; a billing address is current information,
    # and freezing it into a document is how a resend goes to an address the
    # customer abandoned two years ago.
    billing_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 🔴 R7 in miniature: consent is re-checked at dispatch, not at preview,
    # and the flag lives next to the address it suppresses.
    billing_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    billing_opt_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 'warn' | 'require_current' — whether a stale or missing live GSTIN
    #: verification blocks issue for this customer.
    gstin_policy: Mapped[str] = mapped_column(Text, default="warn")

    # 🔴 NOT NULL with a server default ('bronze', 0). `FetchedValue` tells
    # SQLAlchemy to leave them out of an INSERT that does not set them, so the
    # database default applies. Without it SQLAlchemy sends an explicit NULL,
    # which *overrides* the default and violates the constraint — the row is
    # rejected for a column the caller never mentioned.
    #
    # Bronze is the right default anyway: a new record is a lead, not a fact,
    # and Doc 07 says a Bronze record is never messaged.
    quality_tier: Mapped[str] = mapped_column(QUALITY_TIER, server_default=FetchedValue())
    completeness_score: Mapped[int] = mapped_column(SmallInteger, server_default=FetchedValue())

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)

    # 🔴 Nothing is hard-deleted (CLAUDE.md). A tombstone stays resolvable so a
    # stored id never turns into a 404 that looks like the id was wrong.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 🔴 NOT NULL DEFAULT now() in the DDL. Django's `auto_now` set it
    # invisibly; SQLAlchemy does not, so it is set explicitly on every
    # write. Mapped non-nullable so the mismatch is a type error here
    # rather than an IntegrityError from the database at insert time.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    state: Mapped[State | None] = relationship(lazy="joined")
    district: Mapped[District | None] = relationship(lazy="joined")


# ---------------------------------------------------------------------------
# dq — the source register
# ---------------------------------------------------------------------------


class Source(Base):
    """🔴 R1. A collector asserts `is_approved` here before its first request."""

    __tablename__ = "source"
    __table_args__ = {"schema": "dq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(SOURCE_KIND)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_basis: Mapped[str] = mapped_column(Text)
    contains_pii: Mapped[bool] = mapped_column(Boolean, default=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_cadence: Mapped[str | None] = mapped_column(Text, nullable=True)
    licence: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FieldProvenance(Base):
    __tablename__ = "field_provenance"
    __table_args__ = {"schema": "dq"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field_name: Mapped[str] = mapped_column(Text)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("dq.source.id"))
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0.50"))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    source: Mapped[Source] = relationship(lazy="joined")


class Contradiction(Base):
    __tablename__ = "contradiction"
    __table_args__ = {"schema": "dq"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field_name: Mapped[str] = mapped_column(Text)
    value_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_a: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provenance_b: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class Farmer(Base):
    """The partitioned farmer master. Personal data may enter only via an approved PII source."""

    __tablename__ = "farmer"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_id: Mapped[int] = mapped_column(ForeignKey("ref.state.id"), primary_key=True)
    farmer_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    father_or_spouse: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_band: Mapped[str | None] = mapped_column(Text, nullable=True)
    district_id: Mapped[int | None] = mapped_column(ForeignKey("ref.district.id"), nullable=True)
    block_id: Mapped[int | None] = mapped_column(ForeignKey("ref.block.id"), nullable=True)
    village_id: Mapped[int | None] = mapped_column(ForeignKey("ref.village.id"), nullable=True)
    address_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)
    total_area_ha: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    farmer_class: Mapped[str] = mapped_column(FARMER_CLASS, default="unknown")
    primary_crop_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_fpo_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    supplying_mill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    theta_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_tier: Mapped[str] = mapped_column(QUALITY_TIER, server_default=FetchedValue())
    completeness_score: Mapped[int] = mapped_column(SmallInteger, server_default=FetchedValue())
    primary_source_id: Mapped[int | None] = mapped_column(ForeignKey("dq.source.id"), nullable=True)
    consent_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    state: Mapped[State] = relationship(
        primaryjoin="Farmer.state_id == State.id", foreign_keys=[state_id], lazy="joined"
    )
    district: Mapped[District | None] = relationship(
        primaryjoin="Farmer.district_id == District.id", foreign_keys=[district_id], lazy="joined"
    )


# ---------------------------------------------------------------------------
# core — people, their roles, and how to reach them
#
# 🔴 People are not contacts-of-a-company. `core.person` is a human being who
# exists independently of any organisation; `core.person_org_role` is a
# time-bounded statement that they held a post there. A director who moves
# from one FPO to another gets a closed role row and a new one, never an
# overwrite — CLAUDE.md marks that as a decision not to undo, and it is the
# only shape that answers "who was the chairman when we signed this".
# ---------------------------------------------------------------------------


class Person(Base):
    """
    A named human.

    🔴 `full_name` is generated by the database from the three name parts, so
    it is mapped read-only (`FetchedValue`). Writing to it is an error the DDL
    would reject, and the mapping should fail in the same direction.

    `father_or_spouse` reads as optional and is not, in practice: CLAUDE.md
    records that "Ram Kumar" in one district may be four hundred distinct
    people, and name + father/spouse + village + phone is the minimum key that
    separates them. It is nullable because a source may not carry it, not
    because a record without it is finished.
    """

    __tablename__ = "person"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    salutation: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str] = mapped_column(Text)
    middle_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str] = mapped_column(Text, server_default=FetchedValue())
    name_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    father_or_spouse: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[str | None] = mapped_column(GENDER, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Director Identification Number. 🔴 Published by the MCA under the
    #: Companies Act — this is the one identifier on this table that the
    #: statute itself puts in public view, which is why an institutional
    #: collector may write it and may not write a mobile number.
    din: Mapped[str | None] = mapped_column(String(8), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    state_id: Mapped[int | None] = mapped_column(ForeignKey("ref.state.id"), nullable=True)
    district_id: Mapped[int | None] = mapped_column(ForeignKey("ref.district.id"), nullable=True)
    village_id: Mapped[int | None] = mapped_column(ForeignKey("ref.village.id"), nullable=True)

    quality_tier: Mapped[str] = mapped_column(QUALITY_TIER, server_default=FetchedValue())
    primary_source_id: Mapped[int | None] = mapped_column(ForeignKey("dq.source.id"), nullable=True)
    is_farmer: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)

    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    state: Mapped[State | None] = relationship(
        primaryjoin="Person.state_id == State.id", foreign_keys=[state_id], lazy="joined"
    )
    district: Mapped[District | None] = relationship(
        primaryjoin="Person.district_id == District.id", foreign_keys=[district_id], lazy="joined"
    )


class PersonOrgRole(Base):
    """
    A post held at an organisation, bounded in time.

    🔴 `valid_to IS NULL` means "still holds it". Closing a role is setting
    that date, never deleting the row — the register has to be able to answer
    who signed something in 2024, and a deleted row cannot.

    The DDL carries a partial unique index allowing one open primary contact
    per organisation. That constraint is not expressible here and is not
    duplicated here either; the write path lets the database raise it.
    """

    __tablename__ = "person_org_role"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.person.id", ondelete="CASCADE"))
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.organisation.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(ROLE_TYPE)
    designation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_primary_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    is_decision_maker: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("dq.source.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    person: Mapped[Person] = relationship(lazy="joined")
    organisation: Mapped[Organisation] = relationship(lazy="joined")

    @property
    def is_current(self) -> bool:
        return self.valid_to is None


class ContactPoint(Base):
    """
    A phone or an email, with its own lifecycle.

    🔴 Not a column on a person. CLAUDE.md gives the reason in one number:
    rural phone churn runs 15–20% a year, so a phone needs a verification
    state, a delivery-failure counter and a source of its own — none of which
    survives being a `person.phone` string that the next import overwrites.

    `cp_owner_exactly_one` in the DDL requires exactly one of `person_id` and
    `organisation_id`. An organisation switchboard is not personal data; a
    named person's mobile is, whether or not they run a company, and the two
    live in the same table precisely so the ownership column is the thing that
    decides how a row is treated.
    """

    __tablename__ = "contact_point"
    __table_args__ = {"schema": "core"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.person.id", ondelete="CASCADE"), nullable=True
    )
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.organisation.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(CONTACT_KIND)
    value_raw: Mapped[str] = mapped_column(Text)
    #: 🔴 E.164 for phones, lowercased for email. CLAUDE.md: always query this
    #: column, never `value_raw` — "9876543210" and "+91 98765 43210" are the
    #: same number and only one of them matches a literal.
    value_normalised: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(5), default="+91")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    verification: Mapped[str] = mapped_column(VERIFICATION_STATE, default="unverified")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_valid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_failures: Mapped[int] = mapped_column(SmallInteger, default=0)
    bounce_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_whatsapp_capable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("dq.source.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def is_personal(self) -> bool:
        """
        🔴 Whether this row is personal data under DPDP.

        Ownership decides it, not the `kind`. A mobile belonging to a named
        director is personal data; the same number on the organisation is the
        office line. Masking and the audit rule both ask this question.
        """
        return self.person_id is not None
