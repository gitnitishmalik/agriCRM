"""
The admin's Jinja environment, its filters, and paging.

🔴 **PII is masked by default** (R9). `mask_phone` and `mask_email` are what
the templates call; showing a full contact detail needs the
`contact.view_full` capability, which in this build maps to the
`BILLING_OVERRIDE` roles, and it writes `audit.data_access_log`. A console
that renders every phone number in a list view has made the audit log
meaningless — everyone has seen everything, all the time.

🔴 **Money is formatted by `api/money.py`, never by a template.** Indian
grouping is `15,78,250.00`, and a template filter reimplementing it would be a
second implementation to get wrong. The same argument the frontend README
makes about the React client applies here.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from backend.money import format_inr

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"

#: 🔴 CLAUDE.md: timestamps are stored UTC and displayed Asia/Kolkata. An
#: operator reading "issued at 18:30" needs that to be the time they issued it.
DISPLAY_TZ = ZoneInfo("Asia/Kolkata")


def inr(value: Decimal | int | str | None) -> str:
    if value is None or value == "":
        return "—"
    return format_inr(Decimal(str(value)))


def when(value: datetime | date | None, *, with_time: bool = True) -> str:
    """A timestamp in Asia/Kolkata, or an em dash."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        local = value.astimezone(DISPLAY_TZ) if value.tzinfo else value
        return local.strftime("%d %b %Y, %H:%M") if with_time else local.strftime("%d %b %Y")
    return value.strftime("%d %b %Y")


def ago(value: datetime | None) -> str:
    """'3 days ago' — the form an operator scanning a list actually reads."""
    if value is None:
        return "—"
    from datetime import UTC

    delta = datetime.now(UTC) - (value if value.tzinfo else value.replace(tzinfo=UTC))
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "just now"
    for size, label in (
        (86400 * 365, "year"),
        (86400 * 30, "month"),
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
    ):
        if seconds >= size:
            count = seconds // size
            return f"{count} {label}{'s' if count != 1 else ''} ago"
    return "just now"


def mask_phone(value: str | None, *, unmask: bool = False) -> str:
    """
    🔴 R9. `+91*****6096` unless the caller holds the capability.

    The last four digits stay because they are what a person uses to recognise
    a number they already know, and they are not enough to dial it.
    """
    if not value:
        return "—"
    if unmask:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+91*****{digits[-4:]}" if len(digits) >= 4 else "•••"


def mask_email(value: str | None, *, unmask: bool = False) -> str:
    if not value:
        return "—"
    if unmask:
        return value
    local, _, domain = value.partition("@")
    if not domain:
        return "•••"
    return f"{local[:2]}•••@{domain}"


def mask_gstin(value: str | None, *, unmask: bool = True) -> str:
    """
    A GSTIN is a business identifier, not personal data, so it is shown in
    full by default — masking it would make the register useless for the exact
    job it exists for (INVOICE.md §3, D1/D2).
    """
    return value or "—"


def chip(text: str | None, kind: str = "mute") -> Markup:
    if not text:
        return Markup('<span class="chip mute">—</span>')
    return Markup(f'<span class="chip {escape(kind)}">{escape(text)}</span>')


#: Status → chip colour, in one place so a list and a detail page cannot
#: disagree about whether "on_hold" is a warning.
STATUS_KIND = {
    "draft": "mute",
    "issued": "ok",
    "part_paid": "warn",
    "paid": "ok",
    "on_hold": "warn",
    "cancelled": "bad",
    "discarded": "mute",
    "pending": "warn",
    "confirmed": "warn",
    "applied": "ok",
    "rejected": "mute",
    "expired": "mute",
    "failed": "bad",
    "queued": "warn",
    "claimed": "warn",
    "sent": "ok",
    "delivered": "ok",
    "processed": "ok",
    "duplicate": "mute",
    "unmatched": "bad",
    "signature_failed": "bad",
    "replayed": "bad",
    "error": "bad",
    "valid_active": "ok",
    "valid_inactive": "warn",
    "verification_unavailable": "warn",
    "not_found": "warn",
    "invalid_format": "bad",
    "approved": "ok",
    "under_review": "warn",
    "ai_suggested": "warn",
    "superseded": "mute",
    "gold": "gold",
    "silver": "silver",
    "bronze": "bronze",
    "quarantine": "bad",
}


def status_chip(value: str | None) -> Markup:
    return chip(value, STATUS_KIND.get(value or "", "mute"))


def truncate(value: str | None, length: int = 70) -> str:
    if not value:
        return "—"
    text = str(value)
    return text if len(text) <= length else text[: length - 1] + "…"


def confidence_chip(value: Decimal | float | None) -> Markup:
    """
    🔴 The upsert rule in one glance: field-verified is 0.95, a scraped
    registry 0.60, and an incoming value must beat the stored one by 0.15 or it
    writes a contradiction instead. The colour is that threshold made visible.
    """
    if value is None:
        return chip("—", "mute")
    score = float(value)
    kind = "ok" if score >= 0.9 else "warn" if score >= 0.55 else "bad"
    return chip(f"{score:.2f}", kind)


@dataclass
class Page:
    """Offset paging. Enough for a console; a cursor is for an API."""

    items: list[Any]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.per_page)) if self.per_page else 1

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def first_index(self) -> int:
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last_index(self) -> int:
        return min(self.page * self.per_page, self.total)


def query_string(params: dict[str, Any], **overrides: Any) -> str:
    """Rebuild a query string with some parameters replaced. For pager links."""
    from urllib.parse import urlencode

    merged = {**params, **overrides}
    clean = {k: v for k, v in merged.items() if v not in (None, "", [])}
    return urlencode(clean)


env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters.update(
    inr=inr,
    when=when,
    ago=ago,
    mask_phone=mask_phone,
    mask_email=mask_email,
    mask_gstin=mask_gstin,
    truncate_text=truncate,
)
env.globals.update(
    chip=chip,
    status_chip=status_chip,
    confidence_chip=confidence_chip,
    query_string=query_string,
)


def render(template: str, **context: Any) -> str:
    return env.get_template(f"admin/{template}").render(**context)
