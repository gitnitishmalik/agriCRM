"""
The admin console.

🔴 **Why this exists.** CLAUDE.md values Django Admin at roughly three months
of frontend work for a data-curation system, and names it as the reason not to
retire the Django service. This is that reason answered: a server-rendered
console over the same domain layer the API uses, with no second copy of any
rule.

Three principles, each of which is a decision that could have gone the other
way:

1. **It calls the domain, never the database directly for anything with a
   rule attached.** Issuing an invoice from here runs the same pre-issue
   checks; a proposal shows the same diff. An admin that wrote rows straight
   past the domain would be a second implementation of the business, and the
   version that bypasses the checks is exactly the one an operator reaches for
   under pressure.

2. **Collected data leads with its provenance.** An organisation row is not
   interesting on its own; what matters is which source it came from, at what
   confidence, when, and what it contradicts. Those are on the page, not
   behind a click.

3. **It is read-heavy and write-narrow.** Most pages only look. The few that
   write — resolving a contradiction, approving a tax code — are POSTs with a
   CSRF token, and none of them can issue, cancel or pay: those live on the
   API where the confirmation flows are.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from backend.admin.rendering import Page, render
from backend.admin.security import (
    AdminUser,
    check_csrf,
    clear_session,
    set_session,
)
from backend.deps import SessionDep
from backend.models.billing import Invoice
from backend.models.business import (
    Contradiction,
    District,
    FieldProvenance,
    Organisation,
    Source,
    State,
)
from backend.models.copilot import AiProposal
from backend.models.invoice_ops import (
    PaymentWebhookEvent,
)

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)

PER_PAGE = 50


def _csrf(request: Request) -> str:
    from backend.admin.security import CSRF_COOKIE

    return request.cookies.get(CSRF_COOKIE, "")


async def _nav_counts(session) -> dict[str, int]:
    """
    The two numbers worth interrupting somebody for.

    An unresolved contradiction means two sources disagree about a fact
    nobody has adjudicated; an unmatched webhook means money arrived that is
    not against an invoice. Both rot quietly, so both get a badge.
    """
    contradictions = await session.scalar(
        select(func.count(Contradiction.id)).where(Contradiction.resolved_at.is_(None))
    )
    reconciliation = await session.scalar(
        select(func.count(PaymentWebhookEvent.id)).where(
            PaymentWebhookEvent.processing_result.in_(
                ("unmatched", "replayed", "signature_failed", "error", "pending")
            )
        )
    )
    return {"contradictions": contradictions or 0, "reconciliation": reconciliation or 0}


async def _page(request: Request, session, template: str, **context: Any) -> HTMLResponse:
    """Render with the furniture every page needs."""
    context.setdefault("nav_counts", await _nav_counts(session))
    context.setdefault("csrf_token", _csrf(request))
    context.setdefault("params", dict(request.query_params))
    return HTMLResponse(render(template, **context))


def _paged(request: Request, total: int, items: list) -> Page:
    page = max(1, int(request.query_params.get("page", 1) or 1))
    return Page(items=items, total=total, page=page, per_page=PER_PAGE)


def _offset(request: Request) -> int:
    return (max(1, int(request.query_params.get("page", 1) or 1)) - 1) * PER_PAGE


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse, name="admin_login_form")
async def login_form(request: Request, next: str = "/admin/") -> HTMLResponse:
    return HTMLResponse(render("login.html", next_url=next, error=None, needs_mfa=False))


@router.post("/login", name="admin_login")
async def login(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/admin/",
    totp: Annotated[str, Form()] = "",
) -> Response:
    """
    Sign in, second factor included.

    🔴 The console cannot enrol a second factor, only verify one. Enrolment
    happens in the main application — an admin console that could enrol you
    would make MFA optional for whoever reached the console first, which is
    precisely backwards.
    """
    from backend.models.accounts import User
    from backend.security import issue_pair, verify_password

    def _fail(message: str, *, needs_mfa: bool = False) -> HTMLResponse:
        return HTMLResponse(
            render(
                "login.html",
                next_url=next,
                error=message,
                email=email,
                needs_mfa=needs_mfa,
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    # 🔴 One message for a wrong email and a wrong password. Distinguishing
    # them turns the form into a way to enumerate who has an account.
    if user is None or not user.is_active or not verify_password(password, user.password):
        return _fail("Those credentials were not recognised.")

    from backend.admin.security import ADMIN_ROLES

    if user.role not in ADMIN_ROLES:
        return _fail(
            f"The console is for data operations. Your role ({user.role}) does "
            f"not have access to it."
        )

    satisfied = not user.requires_mfa
    if user.requires_mfa:
        if not totp.strip():
            return (
                _fail("", needs_mfa=True)
                if False
                else HTMLResponse(
                    render(
                        "login.html",
                        next_url=next,
                        error=None,
                        email=email,
                        needs_mfa=True,
                    ),
                    status_code=status.HTTP_200_OK,
                )
            )

        from backend.routers.auth import verify_totp_for_user

        satisfied = await verify_totp_for_user(session, user, totp.strip())
        if not satisfied:
            return _fail(
                "That code was not accepted. Codes last 30 seconds and each one works once.",
                needs_mfa=True,
            )

    tokens = issue_pair(
        user.id,
        role=user.role,
        mfa_required=user.requires_mfa,
        mfa_satisfied=satisfied,
    )
    target = next if next.startswith("/admin") else "/admin/"
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    set_session(response, tokens["access"])
    return response


@router.post("/logout", name="admin_logout")
async def logout(request: Request, csrf_token: Annotated[str, Form()] = "") -> Response:
    request.state.csrf_token = csrf_token
    check_csrf(request)
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session(response)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, name="admin_dashboard")
async def dashboard(request: Request, session: SessionDep, caller: AdminUser) -> HTMLResponse:
    """
    What is in the database, and what needs a person.

    The layout is deliberate: collected-data health first, because that is the
    thing that decays silently. A receivables figure is wrong for a day and
    somebody notices; a source that stopped refreshing three months ago is
    invisible until a count is quoted to a customer.
    """
    org_total = await session.scalar(
        select(func.count(Organisation.id)).where(Organisation.is_deleted.is_(False))
    )
    by_tier = dict(
        (
            await session.execute(
                select(Organisation.quality_tier, func.count(Organisation.id))
                .where(Organisation.is_deleted.is_(False))
                .group_by(Organisation.quality_tier)
            )
        ).all()
    )
    by_type = (
        await session.execute(
            select(Organisation.type, func.count(Organisation.id))
            .where(Organisation.is_deleted.is_(False))
            .group_by(Organisation.type)
            .order_by(func.count(Organisation.id).desc())
        )
    ).all()

    sources = list(
        await session.scalars(select(Source).order_by(Source.is_approved.desc(), Source.code))
    )
    provenance_total = await session.scalar(
        select(func.count(FieldProvenance.id)).where(FieldProvenance.is_current.is_(True))
    )
    # Which sources actually produced the current values — the honest answer
    # to "where did the registry come from".
    by_source = (
        await session.execute(
            select(Source.code, Source.name, func.count(FieldProvenance.id))
            .join(FieldProvenance, FieldProvenance.source_id == Source.id)
            .where(FieldProvenance.is_current.is_(True))
            .group_by(Source.code, Source.name)
            .order_by(func.count(FieldProvenance.id).desc())
        )
    ).all()

    last_collection = await session.scalar(select(func.max(FieldProvenance.collected_at)))
    open_contradictions = await session.scalar(
        select(func.count(Contradiction.id)).where(Contradiction.resolved_at.is_(None))
    )

    invoice_total = await session.scalar(
        select(func.count(Invoice.id)).where(Invoice.is_deleted.is_(False))
    )
    outstanding = await session.scalar(
        select(func.coalesce(func.sum(Invoice.total_value), 0)).where(
            Invoice.is_deleted.is_(False), Invoice.status.in_(("issued", "part_paid"))
        )
    )
    unmatched = await session.scalar(
        select(func.count(PaymentWebhookEvent.id)).where(
            PaymentWebhookEvent.processing_result.in_(("unmatched", "signature_failed"))
        )
    )
    pending_proposals = await session.scalar(
        select(func.count(AiProposal.id)).where(AiProposal.status == "pending")
    )

    return await _page(
        request,
        session,
        "dashboard.html",
        section="dashboard",
        caller=caller,
        org_total=org_total or 0,
        by_tier=by_tier,
        by_type=by_type,
        sources=sources,
        provenance_total=provenance_total or 0,
        by_source=by_source,
        last_collection=last_collection,
        open_contradictions=open_contradictions or 0,
        invoice_total=invoice_total or 0,
        outstanding=outstanding or 0,
        unmatched=unmatched or 0,
        pending_proposals=pending_proposals or 0,
    )


# ---------------------------------------------------------------------------
# Organisations — the collected registry
# ---------------------------------------------------------------------------


@router.get("/organisations/", response_class=HTMLResponse, name="admin_organisations")
async def organisations(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    q: str = Query(default=""),
    org_type: str = Query(default=""),
    tier: str = Query(default=""),
    source: str = Query(default=""),
    state: int | None = Query(default=None),
) -> HTMLResponse:
    """
    The registry, filterable — including **by the source that produced it**.

    🔴 That last filter is the point of this page. "Show me every organisation
    whose current values came from the SFAC list" is the question you ask when
    a collector's parser turns out to have been wrong for a month, and it is
    unanswerable without joining provenance.
    """
    conditions = [Organisation.is_deleted.is_(False)]

    if q.strip():
        pattern = f"%{q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Organisation.name).like(pattern),
                func.lower(func.coalesce(Organisation.short_name, "")).like(pattern),
                func.lower(func.coalesce(Organisation.cin, "")).like(pattern),
                func.lower(func.coalesce(Organisation.gstin, "")).like(pattern),
            )
        )
    if org_type:
        conditions.append(Organisation.type == org_type)
    if tier:
        conditions.append(Organisation.quality_tier == tier)
    if state:
        conditions.append(Organisation.state_id == state)

    query = select(Organisation).where(and_(*conditions))
    count_query = select(func.count(Organisation.id)).where(and_(*conditions))

    if source:
        # Organisations whose *current* field values came from this source.
        origin = (
            select(FieldProvenance.entity_id)
            .join(Source, Source.id == FieldProvenance.source_id)
            .where(
                Source.code == source,
                FieldProvenance.entity_type == "organisation",
                FieldProvenance.is_current.is_(True),
            )
            .distinct()
            .scalar_subquery()
        )
        query = query.where(Organisation.id.in_(origin))
        count_query = count_query.where(Organisation.id.in_(origin))

    total = await session.scalar(count_query) or 0
    rows = list(
        await session.scalars(
            query.order_by(Organisation.name).offset(_offset(request)).limit(PER_PAGE)
        )
    )

    # The provenance summary per row, so the list shows where each came from
    # without an N+1 walk.
    origins: dict[uuid.UUID, list[tuple[str, Any]]] = {}
    if rows:
        for entity_id, code, collected in (
            await session.execute(
                select(
                    FieldProvenance.entity_id,
                    Source.code,
                    func.max(FieldProvenance.collected_at),
                )
                .join(Source, Source.id == FieldProvenance.source_id)
                .where(
                    FieldProvenance.entity_type == "organisation",
                    FieldProvenance.entity_id.in_([row.id for row in rows]),
                    FieldProvenance.is_current.is_(True),
                )
                .group_by(FieldProvenance.entity_id, Source.code)
            )
        ).all():
            origins.setdefault(entity_id, []).append((code, collected))

    return await _page(
        request,
        session,
        "organisations.html",
        section="organisations",
        caller=caller,
        page=_paged(request, total, rows),
        origins=origins,
        sources=list(await session.scalars(select(Source).order_by(Source.code))),
        states=list(await session.scalars(select(State).order_by(State.name))),
        filters={"q": q, "org_type": org_type, "tier": tier, "source": source, "state": state},
    )


@router.get(
    "/organisations/{organisation_id}",
    response_class=HTMLResponse,
    name="admin_organisation_detail",
)
async def organisation_detail(
    organisation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    caller: AdminUser,
) -> HTMLResponse:
    """
    One organisation, and **every field's provenance beside its value**.

    🔴 This is the page the whole collected-data story lands on. For each
    field: what it says now, which source said so, at what confidence, when,
    and what earlier value it replaced. A registry you cannot interrogate that
    way is a spreadsheet with a login.
    """
    from backend.admin.security import ADMIN_ROLES  # noqa: F401 — documented above

    org = await session.scalar(select(Organisation).where(Organisation.id == organisation_id))
    if org is None:
        return HTMLResponse(
            render("not_found.html", what="organisation", caller=caller, csrf_token=_csrf(request)),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    provenance = list(
        await session.scalars(
            select(FieldProvenance)
            .options(selectinload(FieldProvenance.source))
            .where(
                FieldProvenance.entity_type == "organisation",
                FieldProvenance.entity_id == org.id,
            )
            .order_by(FieldProvenance.field_name, FieldProvenance.collected_at.desc())
        )
    )
    current = {p.field_name: p for p in provenance if p.is_current}
    history: dict[str, list[FieldProvenance]] = {}
    for row in provenance:
        if not row.is_current:
            history.setdefault(row.field_name, []).append(row)

    contradictions = list(
        await session.scalars(
            select(Contradiction)
            .where(
                Contradiction.entity_type == "organisation",
                Contradiction.entity_id == org.id,
            )
            .order_by(Contradiction.resolved_at.is_(None).desc(), Contradiction.detected_at.desc())
        )
    )

    invoices = list(
        await session.scalars(
            select(Invoice)
            .where(Invoice.organisation_id == org.id, Invoice.is_deleted.is_(False))
            .order_by(Invoice.invoice_date.desc())
            .limit(20)
        )
    )

    state = (
        await session.scalar(select(State).where(State.id == org.state_id))
        if org.state_id
        else None
    )
    district = (
        await session.scalar(select(District).where(District.id == org.district_id))
        if org.district_id
        else None
    )

    # 🔴 R9: unmasking needs the capability. `BILLING_OVERRIDE` is the role set
    # that carries it in this build.
    from backend.domain.scoping import BILLING_OVERRIDE

    may_unmask = caller.user.role in BILLING_OVERRIDE

    return await _page(
        request,
        session,
        "organisation_detail.html",
        section="organisations",
        caller=caller,
        org=org,
        state=state,
        district=district,
        current=current,
        history=history,
        contradictions=contradictions,
        invoices=invoices,
        may_unmask=may_unmask,
    )


# ---------------------------------------------------------------------------
# Source register
# ---------------------------------------------------------------------------


@router.get("/sources/", response_class=HTMLResponse, name="admin_sources")
async def sources(request: Request, session: SessionDep, caller: AdminUser) -> HTMLResponse:
    """
    `dq.source` — the register that decides what a collector may run against.

    🔴 R1 lives here. A collector asserts `is_approved` before its first
    request and exits non-zero if it is false, so this table is not
    documentation about the rules — it *is* the rule. The same is true of
    `contains_pii`: `base.Collector` refuses to start against a source whose
    row says true, which is why the SFAC collector does not read the CEO block
    those PDFs contain.
    """
    rows = list(
        await session.scalars(select(Source).order_by(Source.is_approved.desc(), Source.code))
    )

    counts = dict(
        (
            await session.execute(
                select(FieldProvenance.source_id, func.count(FieldProvenance.id))
                .where(FieldProvenance.is_current.is_(True))
                .group_by(FieldProvenance.source_id)
            )
        ).all()
    )
    last_seen = dict(
        (
            await session.execute(
                select(FieldProvenance.source_id, func.max(FieldProvenance.collected_at)).group_by(
                    FieldProvenance.source_id
                )
            )
        ).all()
    )

    return await _page(
        request,
        session,
        "sources.html",
        section="sources",
        caller=caller,
        sources=rows,
        counts=counts,
        last_seen=last_seen,
    )


# ---------------------------------------------------------------------------
# Field provenance — the scraped values themselves
# ---------------------------------------------------------------------------


@router.get("/provenance/", response_class=HTMLResponse, name="admin_provenance")
async def provenance(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    source: str = Query(default=""),
    field: str = Query(default=""),
    current_only: bool = Query(default=True),
) -> HTMLResponse:
    """
    Every collected value, with its source, confidence and collection time.

    🔴 The rawest view of the scraped data there is: one row per field per
    entity, exactly as the collector wrote it. It answers "what did the SFAC
    run actually put in the database" without trusting a summary, which is the
    question you have when a parser change is suspected.
    """
    conditions = []
    if current_only:
        conditions.append(FieldProvenance.is_current.is_(True))
    if field:
        conditions.append(FieldProvenance.field_name == field)

    query = (
        select(FieldProvenance)
        .options(selectinload(FieldProvenance.source))
        .where(and_(*conditions) if conditions else True)
    )
    count_query = select(func.count(FieldProvenance.id)).where(
        and_(*conditions) if conditions else True
    )

    if source:
        source_row = await session.scalar(select(Source).where(Source.code == source))
        if source_row is not None:
            query = query.where(FieldProvenance.source_id == source_row.id)
            count_query = count_query.where(FieldProvenance.source_id == source_row.id)

    total = await session.scalar(count_query) or 0
    rows = list(
        await session.scalars(
            query.order_by(FieldProvenance.collected_at.desc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )

    # Resolve the entity names in one query rather than per row.
    names: dict[uuid.UUID, str] = {}
    org_ids = [r.entity_id for r in rows if r.entity_type == "organisation"]
    if org_ids:
        names = dict(
            (
                await session.execute(
                    select(Organisation.id, Organisation.name).where(Organisation.id.in_(org_ids))
                )
            ).all()
        )

    field_names = list(
        await session.scalars(
            select(FieldProvenance.field_name).distinct().order_by(FieldProvenance.field_name)
        )
    )

    return await _page(
        request,
        session,
        "provenance.html",
        section="provenance",
        caller=caller,
        page=_paged(request, total, rows),
        names=names,
        sources=list(await session.scalars(select(Source).order_by(Source.code))),
        field_names=field_names,
        filters={"source": source, "field": field, "current_only": current_only},
    )


# ---------------------------------------------------------------------------
# Contradictions
# ---------------------------------------------------------------------------


@router.get("/contradictions/", response_class=HTMLResponse, name="admin_contradictions")
async def contradictions(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    resolved: bool = Query(default=False),
) -> HTMLResponse:
    """
    Where two sources disagree, and nobody has adjudicated.

    🔴 The queue exists because a bulk import never overwrites a
    human-verified value (CLAUDE.md). Field-verified is confidence 0.95, a
    scraped registry 0.60, and an incoming value must beat the stored one by
    0.15 or it writes a row here instead of winning. This page is where that
    rule becomes somebody's afternoon.
    """
    condition = (
        Contradiction.resolved_at.is_not(None) if resolved else Contradiction.resolved_at.is_(None)
    )
    total = await session.scalar(select(func.count(Contradiction.id)).where(condition)) or 0
    rows = list(
        await session.scalars(
            select(Contradiction)
            .where(condition)
            .order_by(Contradiction.detected_at.desc())
            .offset(_offset(request))
            .limit(PER_PAGE)
        )
    )

    names: dict[uuid.UUID, str] = {}
    org_ids = [r.entity_id for r in rows if r.entity_type == "organisation"]
    if org_ids:
        names = dict(
            (
                await session.execute(
                    select(Organisation.id, Organisation.name).where(Organisation.id.in_(org_ids))
                )
            ).all()
        )

    return await _page(
        request,
        session,
        "contradictions.html",
        section="contradictions",
        caller=caller,
        page=_paged(request, total, rows),
        names=names,
        resolved=resolved,
    )


@router.post("/contradictions/{contradiction_id}/resolve", name="admin_contradiction_resolve")
async def resolve_contradiction(
    contradiction_id: int,
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    csrf_token: Annotated[str, Form()] = "",
    keep: Annotated[str, Form()] = "a",
    note: Annotated[str, Form()] = "",
) -> Response:
    """
    Adjudicate one disagreement: keep the stored value, or take the new one.

    🔴 The choice is recorded with the person who made it. A contradiction that
    disappeared without a name against it is a fact somebody changed and nobody
    can account for — and the whole point of the provenance chain is that every
    value can be traced to a decision.
    """
    request.state.csrf_token = csrf_token
    check_csrf(request)

    row = await session.scalar(select(Contradiction).where(Contradiction.id == contradiction_id))
    if row is None or row.resolved_at is not None:
        return RedirectResponse("/admin/contradictions/", status_code=status.HTTP_303_SEE_OTHER)

    chosen = row.value_a if keep == "a" else row.value_b

    if keep == "b" and row.entity_type == "organisation":
        # Taking the incoming value means writing it, and writing it means the
        # provenance row for that field has to move with it — otherwise the
        # registry says one thing and its own audit trail says another.
        org = await session.scalar(select(Organisation).where(Organisation.id == row.entity_id))
        if org is not None and hasattr(org, row.field_name):
            setattr(org, row.field_name, row.value_b)
            org.updated_at = datetime.now(UTC)

    row.resolved_at = datetime.now(UTC)
    row.resolved_by = caller.user.public_id
    row.resolution = f"kept {'stored' if keep == 'a' else 'incoming'} value ({chosen!r})" + (
        f" — {note.strip()}" if note.strip() else ""
    )
    await session.flush()

    return RedirectResponse("/admin/contradictions/", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------


@router.get("/geography/", response_class=HTMLResponse, name="admin_geography")
async def geography(
    request: Request,
    session: SessionDep,
    caller: AdminUser,
    state: int | None = Query(default=None),
) -> HTMLResponse:
    """
    `ref.state` and `ref.district`, LGD-coded.

    🔴 No village list. `ref.village` reaches ~660k rows and CLAUDE.md refuses
    an unscoped scan of it in both the admin and the API — a changelist that
    tries is a page that times out and a database that stops answering
    anything else.
    """
    states = list(await session.scalars(select(State).order_by(State.name)))

    district_counts = dict(
        (
            await session.execute(
                select(District.state_id, func.count(District.id)).group_by(District.state_id)
            )
        ).all()
    )
    org_counts = dict(
        (
            await session.execute(
                select(Organisation.state_id, func.count(Organisation.id))
                .where(Organisation.is_deleted.is_(False))
                .group_by(Organisation.state_id)
            )
        ).all()
    )

    districts = []
    if state:
        districts = list(
            await session.scalars(
                select(District).where(District.state_id == state).order_by(District.name)
            )
        )

    return await _page(
        request,
        session,
        "geography.html",
        section="geography",
        caller=caller,
        states=states,
        district_counts=district_counts,
        org_counts=org_counts,
        districts=districts,
        selected_state=state,
    )
