"""
`crm.stored_object` — the index of every file this module holds.

Uploaded invoices, rendered PDFs and raw webhook payloads all land here. The
bytes live wherever the backend puts them; this row is what makes them
findable, hashable and expirable without opening a file.

🔴 The hash is the point. "The PDF you hold is the PDF you sent" is only
checkable if the sha256 was recorded at the moment of sending, and a delivery
row that names a storage key alone proves nothing — the file behind a key can
be replaced.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base


class StoredObject(Base):
    __tablename__ = "stored_object"
    __table_args__ = {"schema": "crm"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_key: Mapped[str] = mapped_column(Text)
    backend: Mapped[str] = mapped_column(Text, default="local")
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: `invoice_pdf` / `upload` / `webhook_payload` / `gstin_response`.
    #: A retention sweep acts on this rather than on the shape of a key.
    purpose: Mapped[str] = mapped_column(Text)
    retain_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    billing_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crm.billing_entity.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
