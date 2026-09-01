"""
Tenant scoping and permissions for the billing module.

🔴 **What "tenant" means here.** This deployment has one customer — Theta — and
two issuing companies, TFD and TEPL. Everywhere the spec says "tenant", the
boundary implemented is `crm.billing_entity`. Stated once, in one module,
because a scoping rule that means different things in different files is not a
scoping rule.

Two things this module enforces, and both are load-bearing:

1. **The entity is resolved from the caller and the record, never from the
   request body.** A client that could name its own `billing_entity_id` could
   read TFD's invoices while authenticated as a TEPL user, and would do so with
   a perfectly valid token. Every query filters on the resolved value.

2. **Money actions need more than a login.** Issuing, cancelling, recording a
   payment, sending a document and enabling autosend are not CRUD, and a
   `field_agent` token should not carry them just because it can read the
   register.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_session
from backend.deps import Caller, CurrentUser
from backend.models.billing import BillingEntity

# ---------------------------------------------------------------------------
# Permissions
#
# Named for the action rather than the role, so a role change is one edit here
# and the call sites keep reading as English. Doc 12 §1 owns the role list.
# ---------------------------------------------------------------------------

#: Read the register, the ageing report, a proposal, a delivery history.
BILLING_READ = frozenset(
    {
        "field_agent",
        "bd_manager",
        "project_manager",
        "data_ops",
        "leadership",
        "compliance",
        "admin",
    }
)

#: Create and edit a draft, run checks, ask the copilot for a proposal.
BILLING_WRITE = frozenset({"bd_manager", "project_manager", "data_ops", "admin"})

#: 🔴 Issue, cancel, record a payment. The point of no return, and the reason
#: `field_agent` is absent: an agent in the field raises the draft, and
#: somebody in the office turns it into a document.
BILLING_ISSUE = frozenset({"project_manager", "data_ops", "admin"})

#: Send a document or a reminder to a customer. Campaign manager is here
#: because outbound messaging is their competence, not because they bill.
BILLING_SEND = frozenset({"project_manager", "data_ops", "campaign_manager", "admin"})

#: 🔴 Override a blocking GSTIN result, or enable reminder autosend. The two
#: places a human takes personal responsibility for a control being off.
BILLING_OVERRIDE = frozenset({"data_ops", "compliance", "admin"})

#: Approve a tax-code knowledge record as verified. Deliberately narrow —
#: INVOICE.md §12.4 puts the CA at the end of this, and compliance is the role
#: that stands in for them in the system.
KNOWLEDGE_APPROVE = frozenset({"data_ops", "compliance", "admin"})


class ScopeError(HTTPException):
    """403 with a message that says which permission was missing."""

    def __init__(self, action: str, role: str) -> None:
        super().__init__(
            status.HTTP_403_FORBIDDEN,
            f"Your role ({role}) cannot {action}. Ask someone with the "
            f"appropriate role to perform it, or ask an administrator to "
            f"change your role.",
        )


def require(caller: Caller, permitted: frozenset[str], action: str) -> None:
    """Raise unless the caller's role is in `permitted`."""
    if caller.user.role not in permitted:
        raise ScopeError(action, caller.user.role)


# ---------------------------------------------------------------------------
# Entity scope
# ---------------------------------------------------------------------------


class EntityScope:
    """
    Which billing entities this caller may act within.

    Today every authenticated user may act for both TFD and TEPL — there is
    one company operating both, and inventing a per-user entity list nobody
    maintains would be theatre. What is *not* theatre is that the scope is
    resolved here rather than read from a request: when the list becomes
    genuinely restrictive, every query already asks this object instead of
    trusting a body field.
    """

    def __init__(self, caller: Caller, entity_ids: list[uuid.UUID]) -> None:
        self.caller = caller
        self.entity_ids = entity_ids

    @property
    def user_id(self) -> uuid.UUID:
        """🔴 `public_id`. The integer pk never crosses into `crm`."""
        return self.caller.user.public_id

    @property
    def role(self) -> str:
        return self.caller.user.role

    def permits(self, entity_id: uuid.UUID | None) -> bool:
        return entity_id is not None and entity_id in self.entity_ids

    def check(self, entity_id: uuid.UUID | None, *, what: str = "record") -> None:
        """
        🔴 404, not 403, for an entity outside the scope.

        A 403 confirms the record exists. Across a tenant boundary that is
        itself a disclosure — "there is an invoice with this id, you just
        cannot see it" is information the caller should not have.
        """
        if not self.permits(entity_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such {what}.")

    def require(self, permitted: frozenset[str], action: str) -> None:
        require(self.caller, permitted, action)


async def _entity_ids(session: AsyncSession) -> list[uuid.UUID]:
    rows = await session.scalars(select(BillingEntity.id))
    return list(rows)


async def get_scope(
    caller: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EntityScope:
    """The caller's scope. Depended on by every route in this module."""
    return EntityScope(caller, await _entity_ids(session))


Scope = Annotated[EntityScope, Depends(get_scope)]
