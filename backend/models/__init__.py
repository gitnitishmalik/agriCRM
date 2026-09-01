"""
Model registry.

🔴 Every mapped module is imported here, and that is not tidiness — it is a
correctness requirement. SQLAlchemy resolves a `ForeignKey("crm.stored_object.id")`
against the tables present in the metadata at mapper-configuration time, so a
module nobody imported leaves a dangling reference and the first query against
the *referring* table raises `NoReferencedTableError`.

The failure is confusing when it happens: `crm.invoice` stops being queryable
because `crm.stored_object` was never imported, and the traceback names
neither the missing import nor the file that should have had it. Importing
every module in one place makes it impossible.
"""

from backend.models import (  # noqa: F401 — imported for their side effect on the registry
    accounts,
    billing,
    business,
    copilot,
    invoice_ops,
    storage,
)
