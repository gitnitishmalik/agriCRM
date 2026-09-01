# AgriCRM API — FastAPI

The complete AgriCRM backend. FastAPI is the only application runtime; it maps
the existing DDL-owned PostgreSQL/Neon schema through SQLAlchemy.

## Run it

```bash
python -m uvicorn backend.main:app --reload --port 8001    # from repository root
python -m backend.run                                      # equivalent launcher
```

| URL | |
|---|---|
| `http://127.0.0.1:8001/api/docs` | Swagger |
| `http://127.0.0.1:8001/api/v1/healthz/` | liveness — process is up |
| `http://127.0.0.1:8001/api/v1/readyz/` | readiness — database answers |

From repository root: `python -m pytest -c backend/pytest.ini -q backend/tests`

## What is ported

| | Django | FastAPI |
|---|---|---|
| Login / refresh / logout | ✅ | ✅ |
| MFA enrol / verify (TOTP) | ✅ | ✅ |
| Password change + revocation | ✅ | ✅ |
| `GET /auth/me/` | ✅ | ✅ |
| Organisations — list, detail, create, update, soft delete | ✅ | ✅ |
| Duplicate blocking (409 + candidates) + bulk assign | ✅ | ✅ |
| Invoice register, summary, detail | ✅ | ✅ |
| Invoice create / edit draft | ✅ | ✅ |
| Invoice issue / cancel / payment | ✅ | ✅ |
| Invoice HTML / PDF / live preview | ✅ | ✅ |
| Invoice extraction agent | ✅ | ✅ |
| Billing entities | ✅ | ✅ |
| Geography — states, districts, blocks, villages, crops | ✅ | ✅ |
| **Every `/api/v1/` endpoint** | 35 | **35** |
| Collectors (SFAC, R1–R4) — CLI, not an endpoint | ✅ | ❌ |
| Django Admin | ✅ | ❌ nothing equivalent |

`api/tests/test_coverage.py` walks both services' routers and fails if any
Django endpoint lacks a counterpart. It is the check behind that table, so the
table cannot quietly stop being true.

## Why both services can run at once

Three things are wire-compatible, and each has a test that fails if it drifts
(`api/tests/test_django_parity.py`):

- **Tokens.** Same `DJANGO_SECRET_KEY`, same HS256, same claim names. A token
  minted by either verifies in the other, so traffic moves one route at a time.
- **Passwords.** Django's PBKDF2 format, implemented directly. Existing users
  sign in through either service; a password changed in one works in the other.
- **Roles.** `MFA_REQUIRED_ROLES` and `CROSS_TERRITORY_ROLES` are compared
  against Django's at test time rather than copied. A role privileged in one
  service and not the other is a hole that only appears once traffic is split.

## 🔴 What the migration must not lose

**MFA is enforced by default, with a declared opt-out list.** The Django
service shipped a phase where `IsMFAVerified` was written, unit tested,
referenced approvingly in three docstrings — and attached to nothing. Every
organisation and invoice endpoint served a privileged pre-MFA token.

`deps.PRE_MFA` is that lesson made structural: a route reachable before the
second factor must be named there with a reason, and
`tests/test_mfa_boundary.py` walks the router and fails on anything else. It
has already caught one omission — `/readyz`, added in this migration and
undeclared.

**Bypasses cannot reach production.** `DEV_NO_AUTH` and `DEV_NO_MFA` both
require `DEBUG` to be on, and `main.lifespan` refuses to start otherwise.
Under Django the equivalent check ran for `runserver` and `migrate` but not
for gunicorn — a lifespan hook has no such gap, because uvicorn calls it
before accepting a connection whatever invoked it.

**The schema stays owned by `sql/schema.sql`.** SQLAlchemy maps it and never
defines it. `Base.metadata.create_all()` is called nowhere and must not be:
the DDL carries partitioning, generated columns and triggers no ORM can
express, and those *are* the compliance controls.

## 🔴 Three controls the migration nearly lost

Each was found by a test rather than by review, and each is the shape of thing
a port drops silently — nothing fails, a rule simply stops existing.

**Postgres enum columns.** The DDL declares twenty-odd enum types. Mapping
`crm.invoice_status` as `Text` works under psycopg, which adapts, and fails
under asyncpg with `operator does not exist: crm.invoice_status <> character
varying`. A list endpoint with no filter passes; the same endpoint with
`?status=issued` does not. `api/models/types.py` names every type, with
`create_type=False` so SQLAlchemy can never create or alter one.

**Unknown query parameters.** Django rejected them deliberately — CLAUDE.md:
a typo'd filter that silently does nothing is how someone exports the whole
registry believing they exported one district. FastAPI ignores extras by
default, so `?statu=issued` would have returned every invoice with a 200.
`deps.reject_unknown_filters` reinstates it, reading each route's own declared
parameters so the two cannot drift.

**Money formatting.** Indian grouping is `15,78,250.00`, not `1,578,250.00`.
`api/money.py` is compared against `apps/billing/money.py` digit-for-digit
across magnitudes, because during a migration both services answer and a
receivables total that differs by service is a bug an accountant finds first.

## Two driver notes, both found the hard way

**asyncpg, not psycopg, for this service.** psycopg refuses to run async on
Windows' default ProactorEventLoop, and uvicorn installs that policy itself —
so setting a different one beforehand does not survive. The service came up,
`/healthz` answered 200, and every database call failed. Django keeps psycopg;
two drivers is a real cost, smaller than a service that only works if nobody
develops on Windows.

**`NullPool` in tests.** An asyncpg connection belongs to the event loop that
opened it and pytest-asyncio gives each test a fresh one, so a pooled
connection reaching a second test is bound to a closed loop. Every test passed
alone and the suite failed, with the traceback pointing at `pool_pre_ping`.

## 🔴 Four more the DDL caught

The business schema is owned by `sql/schema.sql`, and mapping it with a second
ORM found four things Django had been handling invisibly. Each was an insert
that Postgres rejected outright — which is the right way to find them.

**`quantity_ha` is `GENERATED ALWAYS`.** A comment saying "never write to it"
is not a mechanism; SQLAlchemy included it in the INSERT anyway. Declared
`Computed(..., persisted=True)` so it is left out of INSERT and UPDATE
entirely.

**`updated_at` is NOT NULL.** Django's `auto_now` set it invisibly. Now set
explicitly on every write, and mapped non-nullable so a miss is a type error
rather than an IntegrityError.

**`quality_tier` and `completeness_score` are NOT NULL with server defaults.**
SQLAlchemy sent an explicit NULL, which *overrides* a default — the row was
rejected for a column the caller never mentioned. `FetchedValue()` leaves them
out so the database default applies. Bronze is the right default anyway: a new
record is a lead, not a fact.

**Postgres enum types** — see above.

## What is left

Every HTTP endpoint is ported. Two things are not, and neither is an endpoint:

1. **Collectors.** `manage.py run_collector sfac` is a command, not a route.
   `fetch.py` and `sfac.py` have one Django reference between them; the work is
   the ORM calls in `upsert.py`. Until then, run collections from Django — they
   write to the same database either service reads.
2. **Django Admin.** There is no FastAPI equivalent. `sqladmin` is the nearest
   thing and is not close. CLAUDE.md values Admin at roughly three months of
   frontend work for a data-curation system. **This is the reason not to delete
   the Django service**, and it is a product decision rather than a porting
   task.

Also outstanding in both services: **RLS session variables**.
`User.rls_context()` is mapped but nothing sets it on the connection yet —
Phase 3, and it was never done under Django either.

One dependency note: PDF rendering needs WeasyPrint (`pip install weasyprint`)
and its system libraries. Without it `/pdf` answers 501 naming what to install,
and `/html` serves the same document. An honest 501 beats a 500.

## Data integrity

Nothing was migrated. A second service was added that reads and writes the same
schema, so the rows never moved. Verified after the port:

    accounts_user            8        crm.invoice              3
    core.organisation      746        crm.invoice_payment      2
    dq.field_provenance  4,923        ref.state               36

    total invoiced   15,78,250.00     outstanding   8,85,000.00

Both services return that summary byte-for-byte, and a token minted by either
is accepted by the other. Every write test runs inside a transaction that is
rolled back — confirmed by checking afterwards that no test row survived.

One asymmetry worth knowing: **Django cannot serve a login without Redis**
(axes and throttling); FastAPI has no such dependency and stayed up when the
container stopped.

## Cutting over

The frontend proxies `/api` to one origin (`frontend/vite.config.ts`). Move
routes by pointing that proxy at 8001 for the paths that are ported and
leaving the rest on 8000 — a per-path rewrite, not a switch. Keep both running
until the list above is empty, then retire Django.

---

# The advanced invoice module

INVOICE.md phases I-7 to I-10, built on the same domain layer the register
uses. 78 endpoints, a data-operations console at `/admin`, and 185 tests.

## The one idea

> The outcome is not "an AI that can generate invoices". It is an auditable
> billing system in which AI reduces typing and surfaces risk, while
> deterministic code, database constraints and authorised humans keep control
> of money, tax, numbering, delivery and statutory actions.

Everything below is that sentence made structural.

## 🔴 The trust boundary, and why it holds

**`crm.ai_proposal_action` has four members.** `create_draft`,
`update_draft`, `suggest_organisation_update`, `explain_total`. There is no
`issue`, no `cancel`, no `record_payment`, no `send`. An action the copilot
cannot *name* is an action it cannot request, and adding one is a schema change
a person makes on purpose. `test_the_action_vocabulary_has_no_issue_or_pay`
holds it.

**Unsafe requests are refused before a provider is called.**
`providers/copilot.guard_intent` screens the request text, so no model ever
sees "issue this invoice". A model asked to do it and declining is one prompt
away from not declining. The refusal is *recorded* as a failed proposal — a
refusal nobody counts is a refusal nobody can prove kept happening.

**Confirmation binds to a hash that includes the before-state.** A human
confirms exactly these bytes. If the draft moved underneath — someone edited it
in another tab — the hash no longer matches and the apply is refused rather
than overwriting an edit nobody reviewed. The same mechanism confirms delivery
previews and reminder batches.

**The patch allow-list is an allow-list.** A deny-list has to anticipate every
dangerous field; this has to anticipate every safe one. `invoice_no`,
`status`, `taxable_value`, `line_total` and `billing_entity_id` are not on it,
and a patch naming one is rejected rather than having it stripped — an ignored
field is a change the human approved in the diff and the system did not make.

**Money never passes through a model.** A proposal carries a quantity and a
rate; `money.py` computes every amount. "Explain this total" is a server-side
arithmetic trace the model may paraphrase and cannot supply figures for.

## 🔴 Controls worth knowing before you change anything

**The pre-issue checks run inside `issue_invoice`, not only on the screen.** A
client that never calls `/checks/`, or calls it and ignores the answer, still
cannot issue an invoice with a malformed GSTIN. A control that depends on a UI
remembering to ask is not a control.

**"Not checked" is never rendered as "checked and fine".** Operation logs land
in Phase 3 and the satellite cross-check in Phase 5. Until then those checks
return `not_available` with a reason. Collapsing them into a green tick would
be a false assurance about the exact question this system exists to answer: did
we bill for more acres than we sprayed.

**Provider downtime is `verification_unavailable`, and it is never cached.** A
GSTIN service that answers "probably fine" when it cannot reach the registry is
worse than none — it produces a confident record of a check that did not
happen. `test_provider_downtime_is_never_reported_as_valid` is the assertion
the feature exists for.

**A payment request is not a payment.** Nothing in `providers/payments.py`
creates an `invoice_payment` row. Only a human-entered receipt, or a signed
webhook whose amount, currency and reference all match an outstanding request,
does. Anything ambiguous goes to the reconciliation queue — a system that
guesses will eventually guess wrong on a large number.

**Webhooks are stored before they are trusted.** Signature verdict first, then
processing. A handler that returns early on a bad signature keeps no record of
what it rejected, and "we started receiving events we could not verify" is
precisely what you want to be able to see afterwards. Unique on
`(provider, provider_event_id)`, so a redelivery cannot create a second
payment.

**An unknown WhatsApp sender is answered with silence.** Not "you are not
registered" — replying at all confirms the endpoint is live and that some
numbers are. The sender-to-entity binding in `crm.messaging_identity` *is* the
authorisation.

**Consent is re-checked at dispatch, not at preview** (R7). A customer who opts
out between confirming and sending does not receive the message, and the
delivery is `cancelled` rather than `failed` — an opt-out is a decision, not an
error, and counting it as one would make the failure rate meaningless.

## The console

`/admin` — server-rendered, over the same domain layer. CLAUDE.md values Django
Admin at roughly three months of frontend work for a data-curation system and
names it the reason not to retire the Django service; this is that reason
answered.

| Page | Shows |
|---|---|
| Dashboard | Collected-data health first, because it decays silently |
| Organisations | The registry, **filterable by the source that produced it** |
| Organisation detail | 🔴 Every field beside its provenance: source, confidence, when, what it superseded |
| Source register | `dq.source` — R1 and R4 made visible, with the legal basis |
| Field provenance | The rawest view: one row per field per entity, as the collector wrote it |
| Contradictions | Where two sources disagree, adjudicated with a name attached |
| Invoices | Register, and a detail page with the checks that ran at issue |
| Receivables | Ageing and the collection ranking, from the same service the API serves |
| Deliveries | Every attempt, each naming the PDF hash it carried |
| Reconciliation | Unmatched, replayed and unsigned events, oldest first |
| Proposals | Every AI proposal, **including the refused ones** |
| Extractions | What the model read beside what was accepted |
| Tax codes | Effective-dated knowledge and its review state |

The console **cannot issue, cancel or record a payment** — those live on the
API behind their confirmation flows, and
`test_the_console_cannot_issue_an_invoice` reads the source to prove the code
path does not exist. Its session is the same JWT the API issues, with the same
MFA rule; a second auth system would be a second set of bugs.

## Running it

```bash
make db-migrate       # idempotent schema additions, safe on a live database
make run              # FastAPI on :8001, console at /admin
make test             # 185 tests
```

Every provider defaults to a deterministic fake. The safety suite therefore
runs on every commit, costs nothing, needs no credentials and cannot reach a
real customer — **a safety test that is skipped for want of a key looks like
coverage and is not.**

## 🔴 Four things the port broke silently, and how they were found

Each of these passed every test and failed in a browser. They are recorded
because they share a shape: **a port that compiles is not a port that works**,
and the tests exercised the JSON paths while the failures lived in the paths
tests did not call.

**The invoice templates were still Django.** All three carried
`|default:"—"`, `{% extends "billing/invoice_base.html" %}` and
`forloop.last` — none of which Jinja understands. The *filters* had been
ported correctly (`render.py` registers Django-compatible `date`, `default`
and `linebreaksbr`); only the call syntax was left behind. Document rendering
raised for T1, T2 and T3, and nothing noticed because a template error
surfaces at render time rather than import time.
`test_every_template_renders` now renders all three.

**Trailing slashes made a redirect loop that read like an expiring session.**
`GET /invoices/{id}/` answered 307 to an *absolute* URL on the backend origin.
Behind the dev proxy that is cross-origin, so the browser stripped
`Authorization`, the retry arrived unauthenticated, the client saw 401 and
refreshed its token — round and round:

```
GET  /api/v1/invoices/{id}/  -> 307
GET  /api/v1/invoices/{id}   -> 401   (header dropped crossing origins)
POST /api/v1/auth/refresh/   -> 200   (client thinks the token died)
```

Both forms are now registered and neither redirects.
`test_no_api_route_redirects_on_a_trailing_slash` walks every route — it
immediately found two more, `/healthz` and `/readyz`, where a health checker
receiving a 307 can score the instance as down.

**The live preview disagreed with its own client twice.** The create screen
binds its dropdown to `entity_code` ("TEPL"); `PreviewRequest` required
`billing_entity` (a UUID), so every keystroke got a 400 and a working renderer
looked broken. And the route returned a bare `text/html` body while the client
reads `result.html` — so even once the 400 was fixed the pane would have
stayed blank on a 200, which is the failure that looks like a rendering bug
and is a contract mismatch.

Preview now takes either identifier, returns JSON carrying the document plus
the pre-formatted figures, and has its own lenient line shape: a controlled
React input holds `""` before anyone types, and an unfilled box is not an
invalid number. **Creating an invoice keeps the strict shape** — the leniency
is confined to a path that saves nothing and allocates no number.

**A missing `pypdf` silently downgraded to the vision path.** That is the path
INVOICE.md measured producing a complete fictional invoice — number "12345"
against a real TEPL/2026-27/08. A PDF with no text layer *is* a scan and
belongs on vision; a PDF whose text we cannot read because a package is
missing is a deployment fault, and the two were indistinguishable. It now
refuses and says so. `pypdf` and `pillow` are both declared in
`api/requirements.txt`; they were not.

**And one that was nobody's bug:** the configured NVIDIA text model was
retired by the provider on 2026-08-26, answering HTTP 410 with nothing changed
locally. The default is now `openai/gpt-oss-20b` — what INVOICE.md I-4a
actually measured as exact — and a 410 names the model and points at
`NVIDIA_TEXT_MODEL` rather than saying "fill the form by hand". 🔴 The whole
Llama family appears to have gone at once; the vision models still answer
today but are worth replacing before they do not.

## 🔴 What is deliberately not built

**No filing.** Nothing obtains an IRN, files GSTR-1, posts to a ledger or moves
money. The Tally and Zoho exports and the GSTR-1 working paper are for your CA
— the payloads say so in a `not_a_filing` field, because the moment somebody is
about to upload a file to a portal is the moment they are not reading
documentation. `test_nothing_in_the_export_module_claims_to_file` greps for
affirmative filing language, and has a self-check so a mangled pattern cannot
make it pass vacuously.

**No tax determination.** §5.4 is unresolved — whether TFD-to-mill billing is a
taxable supply or grant disbursement. The copilot does not choose a treatment,
and says so rather than leaving the omission silent. A tax-code suggestion is
only "verified" once a named CA approved it, and the database refuses
`approved` without a reviewer.

**No page/bbox evidence yet.** `crm.invoice_extraction.evidence` exists and is
empty: neither configured provider returns bounding boxes. Populating it needs
a provider that emits spans, which is a provider decision.

**No credit/debit notes, IRN or GSTR-2B.** Separate statutory projects needing
sandbox certification.
