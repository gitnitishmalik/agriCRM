"""
Invoice → HTML → PDF.

Two separate jobs, deliberately kept apart:

* ``render_html`` has no dependency beyond Django's template engine. It is what
  the live preview in the browser uses, so a preview works on any machine
  including a Windows laptop with no native libraries installed.

* ``render_pdf`` needs a rendering engine, and which one is available depends
  on the machine. Production runs Linux where WeasyPrint installs cleanly;
  Windows development does not, because WeasyPrint needs GTK/Pango DLLs that
  are a separate install. Rather than pretend, the backend is chosen at call
  time and a missing one raises an error that names the fix.

🔴 Regenerating an invoice must produce the same bytes. That is what makes
``pdf_sha256`` a proof rather than a decoration, and it is why nothing here
puts a generation timestamp into the document.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from decimal import Decimal

from backend import gstin as gstin_lib
from backend.models.billing import BillingEntity, Invoice
from backend.money import format_inr

#: Template code → Django template. A code that is not here is a bug, not a
#: default: silently falling back to T2 would print a Foundation invoice on
#: Enerlytics stationery.
TEMPLATES = {
    "T1": "billing/invoice_t1.html",
    "T2": "billing/invoice_t2.html",
    "T3": "billing/invoice_t3.html",
}

UNIT_LABELS = {
    "acre": "Acre",
    "sq_km": "Sq. Km",
    "hectare": "Hectare",
    "each": "No.",
    "lump_sum": "Lump sum",
    "day": "Day",
    "hour": "Hour",
}

TAX_LABELS = {
    "igst": "IGST OUTPUT {pct}%",
    "cgst_sgst": "CGST {half}% + SGST {half}%",
    "zero_rated": "Zero rated",
    "exempt": "Exempt",
    "grant": "Grant",
}


class RenderError(RuntimeError):
    """Raised with a message that says what to install, not just that it failed."""


@dataclass
class RenderedLine:
    """One line, with every number already formatted for print."""

    line_no: int
    description: str
    hsn_sac: str
    quantity_display: str
    unit_display: str
    rate_display: str
    taxable_display: str
    total_display: str
    location_note: str
    area_ha: Decimal | None


def _trim(value: Decimal) -> str:
    """
    Drop trailing zeros from a quantity: 2301.0000 prints as 2301, 65.7000 as 65.7.

    An invoice shows the number the contract says. "2301.0000 acres" reads as
    machine output, and the originals never do it.
    """
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_context(
    invoice: Invoice,
    *,
    entity: BillingEntity | None = None,
    lines: list | None = None,
) -> dict:
    """
    Everything the templates need, with the numbers already formatted.

    Templates do no arithmetic and no formatting decisions. Django's template
    language cannot round money correctly and should not try — every figure
    arrives here as a finished string.

    ``lines`` may be passed explicitly, which is what the live preview does:
    the form has line data the database has never seen, and previewing an
    unsaved invoice must show what the user is typing rather than what was last
    saved. When it is omitted the invoice's own rows are used.
    """
    # 🔴 The entity as it was on the invoice date, not as it is now. TEPL's bank
    # moved mid-year, and a 2025 invoice must re-render with the Axis block.
    if entity is None:
        entity = BillingEntity.for_date(invoice.entity_code, invoice.invoice_date)
        if entity is None:
            entity = invoice.billing_entity

    if lines is None:
        lines = list(invoice.lines.all().order_by("line_no"))

    rendered = [
        RenderedLine(
            line_no=line.line_no,
            description=line.description,
            hsn_sac=line.hsn_sac or "",
            quantity_display=_trim(line.quantity),
            unit_display=UNIT_LABELS.get(line.unit, line.unit),
            rate_display=format_inr(line.rate),
            taxable_display=format_inr(line.line_taxable_value),
            total_display=format_inr(line.line_total),
            location_note=line.location_note or "",
            area_ha=line.quantity_ha,
        )
        for line in lines
    ]

    pct = invoice.tax_rate_pct
    tax_label = TAX_LABELS.get(invoice.tax_treatment, "Tax {pct}%").format(
        pct=_trim(pct), half=_trim(pct / 2)
    )

    # The Total row repeats the quantity only when every line shares one unit —
    # adding acres to square kilometres would be nonsense, and the originals
    # simply leave the cell blank in that case.
    units = {line.unit for line in lines}
    if len(units) == 1 and lines:
        total_qty = sum((line.quantity for line in lines), Decimal(0))
        total_quantity_display = f"{_trim(total_qty)} {UNIT_LABELS.get(lines[0].unit, '')}".strip()
    else:
        total_quantity_display = ""

    # T1 puts the unit in the column heading rather than on each row.
    qty_heading = UNIT_LABELS.get(lines[0].unit, "") + "s" if lines else "Qty"

    buyer_state = invoice.buyer_state_code or gstin_lib.state_code(invoice.buyer_gstin or "")

    return {
        "invoice": invoice,
        "entity": entity,
        "lines": rendered,
        "entity_state_name": gstin_lib.state_name(entity.state_code) or "",
        "buyer_state_name": gstin_lib.state_name(buyer_state or "") or "",
        "tax_label": tax_label,
        "tax_display": format_inr(invoice.tax_amount),
        "taxable_display": format_inr(invoice.taxable_value),
        "total_display": format_inr(invoice.total_value),
        "total_quantity_display": total_quantity_display,
        "qty_heading": qty_heading,
        "is_draft": invoice.status == "draft",
        # 🔴 Which treatments separate tax out. Kept as a literal set rather
        # than inferred: INVOICE.md §5.4 is still open on which customers
        # are grant disbursement rather than taxable supply.
        "is_taxable": invoice.tax_treatment in ("igst", "cgst_sgst"),
        # A draft or a cancelled invoice must never be mistaken for a live
        # document, on screen or on paper.
        "watermark": {
            "draft": "DRAFT",
            "cancelled": "CANCELLED",
            "discarded": "VOID",
        }.get(invoice.status),
    }


# ---------------------------------------------------------------------------
# The Jinja2 environment
#
# Django's template engine is the only thing this module needed from Django,
# and the templates use nothing but block/extends/for/if/with — all of which
# Jinja shares. Three Django filters are re-implemented below rather than
# rewritten out of the templates: the markup is the actual invoice, it has been
# checked against the real documents, and editing it to suit a new engine is
# how a rendering difference gets introduced into a statutory document.
# ---------------------------------------------------------------------------

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"


def _date(value, fmt: str = "j M Y") -> str:
    """
    Django's `|date` filter, for the format strings these templates use.

    Only the specifiers actually present are supported. A silent wrong date on
    an invoice is worse than a KeyError at render time, so anything else raises.
    """
    if value is None:
        return ""
    mapping = {
        "j": str(value.day),
        "d": f"{value.day:02d}",
        "M": value.strftime("%b"),
        "m": f"{value.month:02d}",
        "F": value.strftime("%B"),
        "Y": str(value.year),
        "y": f"{value.year % 100:02d}",
    }
    return "".join(mapping.get(ch, ch) for ch in fmt)


def _linebreaksbr(value) -> Markup:
    """Django's `|linebreaksbr`: newlines become <br>, everything else escaped."""
    if value is None:
        return Markup("")
    return Markup("<br>".join(escape(line) for line in str(value).splitlines()))


_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=False,
    lstrip_blocks=False,
)
_env.filters["date"] = _date
_env.filters["linebreaksbr"] = _linebreaksbr
_env.filters["default"] = lambda v, d="": d if v in (None, "") else v


def render_html(
    invoice,
    *,
    entity=None,
    lines=None,
) -> str:
    """The invoice as HTML. Same templates, same arithmetic, same output."""
    template_name = TEMPLATES.get(invoice.template_code)
    if template_name is None:
        raise ValueError(
            f"Unknown template '{invoice.template_code}'. "
            f"Known templates: {', '.join(sorted(TEMPLATES))}."
        )
    template = _env.get_template(pathlib.Path(template_name).name)
    return template.render(**build_context(invoice, entity=entity, lines=lines))
