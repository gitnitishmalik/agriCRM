"""
PostgreSQL enum columns.

🔴 The DDL declares twenty-odd enum types — `crm.invoice_status`,
`core.org_type`, `dq.quality_tier` and the rest. Mapping one of those columns
as `Text` works under psycopg, which adapts silently, and fails under asyncpg
with:

    operator does not exist: crm.invoice_status <> character varying

That difference is worth stating plainly, because it is the whole reason this
module exists: the Django service never had to name these types, so the
migration had nothing to copy, and the mistake only appears when a query
actually filters on one. A list endpoint with no filter applied passes; the
same endpoint with `?status=issued` does not.

`create_type=False` on every one of them. The types belong to
`agri-crm-docs/sql/schema.sql` and SQLAlchemy must never attempt to create,
alter or drop one — CLAUDE.md: new enum values are appended, never renamed or
removed, and that is a decision made in the DDL by a person.
"""

from __future__ import annotations

from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import ENUM


def pg_enum(schema: str, name: str, *values: str) -> Enum:
    """
    Map an existing PostgreSQL enum type.

    Values are listed so SQLAlchemy can round-trip them, but they are not the
    definition — the DDL is. A value present in the database and missing here
    reads back fine; a value here and missing from the database is a write
    that fails loudly, which is the right way round.
    """
    return ENUM(
        *values,
        name=name,
        schema=schema,
        create_type=False,
        # Values arrive from the driver as plain strings and are compared as
        # strings throughout the application; no Python Enum class is involved.
        native_enum=True,
        validate_strings=False,
    )


# -- crm ---------------------------------------------------------------------

INVOICE_STATUS = pg_enum(
    "crm",
    "invoice_status",
    "draft",
    "issued",
    "on_hold",
    "part_paid",
    "paid",
    "cancelled",
    "discarded",
)

TAX_TREATMENT = pg_enum(
    "crm", "tax_treatment", "igst", "cgst_sgst", "zero_rated", "exempt", "grant"
)

BILLING_UNIT = pg_enum(
    "crm",
    "billing_unit",
    "acre",
    "sq_km",
    "hectare",
    "each",
    "lump_sum",
    "day",
    "hour",
)

# -- core --------------------------------------------------------------------

ORG_TYPE = pg_enum(
    "core",
    "org_type",
    "fpo",
    "acs",
    "sugar_mill",
    "cooperative_federation",
    "input_dealer",
    "ngo_promoting_institution",
    "government_body",
    "private_company",
    "bank_nbfc",
    "other",
)

ORG_STATUS = pg_enum(
    "core", "org_status", "prospect", "active", "dormant", "defunct", "merged", "blacklisted"
)

LEGAL_FORM = pg_enum(
    "core",
    "legal_form",
    "producer_company",
    "cooperative_society",
    "section_8_company",
    "private_limited",
    "public_limited",
    "llp",
    "partnership",
    "proprietorship",
    "trust",
    "society",
    "statutory_body",
    "unregistered",
    "unknown",
)

# -- dq ----------------------------------------------------------------------

QUALITY_TIER = pg_enum("dq", "quality_tier", "gold", "silver", "bronze", "quarantine")

FARMER_CLASS = pg_enum(
    "core", "farmer_class", "marginal", "small", "semi_medium", "medium", "large", "unknown"
)

SOURCE_KIND = pg_enum(
    "dq",
    "source_kind",
    "public_registry",
    "open_government_data",
    "official_website",
    "industry_directory",
    "partner_agreement",
    "field_collection",
    "inbound_signup",
    "theta_analytics",
    "purchased_licensed",
    "manual_entry",
    "inferred",
    "unknown",
)


# -- crm, advanced invoice module --------------------------------------------
#
# All declared in `sql/schema_invoice_advanced.sql`. Listed here for the same
# reason as everything above: a column of one of these types compared against
# a bare string fails under asyncpg, and it fails only once a filter is
# actually applied.

AI_PROPOSAL_STATUS = pg_enum(
    "crm",
    "ai_proposal_status",
    "pending",
    "confirmed",
    "applied",
    "rejected",
    "expired",
    "failed",
)

#: 🔴 The copilot's whole vocabulary of actions. There is no 'issue',
#: 'cancel', 'record_payment' or 'send' member, and adding one is a schema
#: change a person makes on purpose (INVOICE.md §12.2).
AI_PROPOSAL_ACTION = pg_enum(
    "crm",
    "ai_proposal_action",
    "create_draft",
    "update_draft",
    "suggest_organisation_update",
    "explain_total",
)

CHECK_SEVERITY = pg_enum("crm", "check_severity", "info", "warning", "error")

DELIVERY_STATUS = pg_enum(
    "crm",
    "delivery_status",
    "queued",
    "claimed",
    "sent",
    "delivered",
    "failed",
    "cancelled",
)

PAYMENT_REQUEST_STATUS = pg_enum(
    "crm",
    "payment_request_status",
    "created",
    "awaiting_manual_confirmation",
    "pending_provider",
    "succeeded",
    "failed",
    "expired",
    "cancelled",
)

WEBHOOK_PROCESSING_RESULT = pg_enum(
    "crm",
    "webhook_processing_result",
    "pending",
    "processed",
    "duplicate",
    "unmatched",
    "signature_failed",
    "replayed",
    "error",
)

GSTIN_VERIFICATION_STATUS = pg_enum(
    "crm",
    "gstin_verification_status",
    "valid_active",
    "valid_inactive",
    "cancelled",
    "provisional",
    "not_found",
    "invalid_format",
    "verification_unavailable",
    "error",
)

KNOWLEDGE_REVIEW_STATUS = pg_enum(
    "crm",
    "knowledge_review_status",
    "ai_suggested",
    "under_review",
    "approved",
    "rejected",
    "superseded",
)

REMINDER_RUN_STATUS = pg_enum(
    "crm",
    "reminder_run_status",
    "preview",
    "confirmed",
    "sending",
    "completed",
    "cancelled",
    "expired",
)

# -- comm --------------------------------------------------------------------

CHANNEL = pg_enum("comm", "channel", "whatsapp", "sms", "email", "voice", "postal", "in_app")


# -- core, people and contact points -----------------------------------------
#
# Sprint 3. `core.person`, `core.person_org_role` and `core.contact_point` are
# the lawful home for a named human: a row here carries a source and, for a
# contact point, a verification state, which is what makes it usable
# afterwards. The same asyncpg rule applies as everywhere above — a filter on
# one of these columns against a bare string fails without the type named.

GENDER = pg_enum("core", "gender", "male", "female", "other", "undisclosed")

CONTACT_KIND = pg_enum("core", "contact_kind", "mobile", "landline", "whatsapp", "email", "fax")

#: 🔴 `do_not_contact` is a terminal state, not a failure count. A number that
#: reaches it is never dialled again regardless of what a later import says —
#: the same argument `comm.suppression` makes for consent, applied to the
#: contact point itself.
VERIFICATION_STATE = pg_enum(
    "core",
    "verification_state",
    "unverified",
    "pending",
    "verified",
    "failed",
    "invalid",
    "do_not_contact",
)

ROLE_TYPE = pg_enum(
    "core",
    "role_type",
    "managing_director",
    "chief_executive",
    "chairman",
    "vice_chairman",
    "director",
    "secretary",
    "treasurer",
    "board_member",
    "member_farmer",
    "shareholder",
    "cane_manager",
    "procurement_head",
    "general_manager",
    "unit_head",
    "accountant",
    "field_officer",
    "promoter",
    "nodal_officer",
    "other",
)
