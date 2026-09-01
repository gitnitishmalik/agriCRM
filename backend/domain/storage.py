"""
Object storage — one interface, a local backend and an S3 backend.

Uploaded documents, rendered PDFs and raw webhook bodies all go through here.
Two properties matter more than where the bytes land:

1. **Content-addressed.** The key is derived from the sha256, so storing the
   same file twice is one object and the hash in a delivery record always
   resolves.
2. **Indexed in the database.** `crm.stored_object` is written in the same
   transaction as whatever referenced it, so a row can never point at a file
   nobody recorded, and a retention sweep can find every object by purpose
   without listing a bucket.

🔴 There is deliberately no `fetch_url()`. INVOICE.md §11.2 forbids fetching
arbitrary customer URLs — logo discovery in the reference app did exactly that
— and the safe version of that feature is an upload. A module that cannot make
an outbound request cannot be talked into making one.
"""

from __future__ import annotations

import logging
import os
import pathlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.hashing import sha256_bytes
from backend.models.storage import StoredObject

logger = logging.getLogger("backend.storage")

#: Retention by purpose, in days. R12 sets one year for logs; source documents
#: follow the statutory retention a tax invoice needs (8 years, generously
#: rounded), and a webhook payload is diagnostic data that stops being useful
#: long before that.
RETENTION_DAYS: dict[str, int | None] = {
    "invoice_pdf": None,  # None = keep. An issued document is not disposable.
    "upload": 2920,  # 8 years
    "webhook_payload": 365,
    "gstin_response": 365,
    "knowledge_source": None,
}

#: 🔴 What may be uploaded at all. Validated against the file's actual leading
#: bytes, not the name and not the browser's Content-Type — both are supplied
#: by whoever is uploading.
ALLOWED_UPLOAD_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic"}
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class StorageError(RuntimeError):
    """Raised with a message intended for the person who uploaded the file."""


@dataclass(frozen=True)
class Stored:
    """The result of putting bytes somewhere."""

    object_id: uuid.UUID
    storage_key: str
    sha256: bytes
    size_bytes: int
    content_type: str
    backend: str


# ---------------------------------------------------------------------------
# Content sniffing
# ---------------------------------------------------------------------------

#: (offset, magic bytes, mime). Ordered; first match wins.
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"%PDF-", "application/pdf"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (8, b"WEBP", "image/webp"),
    (4, b"ftypheic", "image/heic"),
    (4, b"ftypheix", "image/heic"),
    (4, b"ftypmif1", "image/heic"),
)


def sniff_content_type(content: bytes) -> str | None:
    """
    The file's real type, read from its leading bytes.

    🔴 Never trust the declared type. A `.pdf` name and an `application/pdf`
    header cost nothing to forge, and the parser that opens the file next is
    the thing being protected — a PDF parser handed a crafted image is a
    different attack surface than one handed a PDF.
    """
    for offset, magic, mime in _MAGIC:
        if content[offset : offset + len(magic)] == magic:
            return mime
    return None


def validate_upload(content: bytes, *, declared_name: str = "") -> str:
    """
    Check size and real type. Returns the sniffed content type.

    Raises `StorageError` with a message the uploader can act on — "this looks
    like a Word document" is useful; "unsupported media type" is not.
    """
    if not content:
        raise StorageError("The uploaded file is empty.")

    if len(content) > MAX_UPLOAD_BYTES:
        raise StorageError(
            f"That file is {len(content) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB. A scan at 200 dpi is "
            f"usually well under it."
        )

    sniffed = sniff_content_type(content)
    if sniffed is None:
        suffix = pathlib.Path(declared_name).suffix.lower()
        hint = {
            ".docx": "This looks like a Word document. Export it as PDF first.",
            ".doc": "This looks like a Word document. Export it as PDF first.",
            ".xlsx": "Spreadsheets are not invoices; upload the PDF or a photo.",
            ".zip": "Upload the document itself rather than an archive.",
        }.get(suffix, "")
        raise StorageError(
            "That file is not a PDF or a photo — its contents do not match any "
            f"accepted type. {hint}".strip()
        )

    if sniffed not in ALLOWED_UPLOAD_TYPES:
        raise StorageError(f"{sniffed} files are not accepted here.")

    return sniffed


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class LocalBackend:
    """
    Files under a directory. The default, and what tests use.

    Keys are content-addressed and fanned out two levels, because a flat
    directory of a hundred thousand invoice PDFs is slow to list on every
    filesystem that matters.
    """

    name = "local"

    def __init__(self, root: pathlib.Path | str) -> None:
        self.root = pathlib.Path(root)

    def _path(self, key: str) -> pathlib.Path:
        # 🔴 Keys are generated here, never supplied by a caller — but the
        # check costs nothing and this is the one place a traversal would
        # matter if that ever stopped being true.
        resolved = (self.root / key).resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            raise StorageError("Refusing a storage key that escapes the root.")
        return resolved

    def put(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a crash mid-write leaves a temp file rather than a
        # truncated object whose hash no longer matches its key.
        temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temp.write_bytes(content)
        temp.replace(path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as error:
            raise StorageError(f"No stored object at {key}.") from error

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Backend:
    """
    S3, for deployed environments.

    Imported lazily so the package has no boto3 requirement in development —
    the local backend is the default and the tests never touch this path.
    """

    name = "s3"

    def __init__(self, bucket: str, *, prefix: str = "", region: str | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region

    def _client(self):
        import boto3

        return boto3.client("s3", region_name=self.region)

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, content: bytes) -> None:
        self._client().put_object(Bucket=self.bucket, Key=self._key(key), Body=content)

    def get(self, key: str) -> bytes:
        response = self._client().get_object(Bucket=self.bucket, Key=self._key(key))
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=self._key(key))


def get_backend() -> LocalBackend | S3Backend:
    """The configured backend. S3 when a bucket is named, local otherwise."""
    from backend.config import settings

    if settings.storage_backend == "s3" and settings.storage_bucket:
        return S3Backend(
            settings.storage_bucket,
            prefix=settings.storage_prefix,
            region=settings.aws_region or None,
        )
    return LocalBackend(settings.storage_root)


# ---------------------------------------------------------------------------
# The database-facing API
# ---------------------------------------------------------------------------


def _key_for(digest: bytes, content_type: str) -> str:
    suffix = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "application/json": ".json",
        "text/html": ".html",
    }.get(content_type, "")
    hexed = digest.hex()
    return f"{hexed[:2]}/{hexed[2:4]}/{hexed}{suffix}"


async def store(
    session: AsyncSession,
    content: bytes,
    *,
    content_type: str,
    purpose: str,
    original_name: str | None = None,
    billing_entity_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> Stored:
    """
    Put bytes in the store and index them, returning the row's identity.

    Idempotent by content: storing the same bytes for the same purpose returns
    the existing object rather than a second copy. That is what makes "upload
    this invoice again" cheap and what lets duplicate detection compare file
    hashes without a second table.
    """
    digest = sha256_bytes(content)
    key = _key_for(digest, content_type)
    backend = get_backend()

    existing = await session.scalar(
        select(StoredObject).where(
            StoredObject.backend == backend.name,
            StoredObject.storage_key == key,
            StoredObject.is_deleted.is_(False),
        )
    )
    if existing is not None:
        return Stored(
            object_id=existing.id,
            storage_key=existing.storage_key,
            sha256=existing.sha256,
            size_bytes=existing.size_bytes,
            content_type=existing.content_type,
            backend=existing.backend,
        )

    backend.put(key, content)

    retention = RETENTION_DAYS.get(purpose)
    row = StoredObject(
        storage_key=key,
        backend=backend.name,
        content_type=content_type,
        size_bytes=len(content),
        sha256=digest,
        original_name=original_name,
        purpose=purpose,
        retain_until=(date.today() + timedelta(days=retention) if retention is not None else None),
        billing_entity_id=billing_entity_id,
        created_at=datetime.now(UTC),
        created_by=created_by,
    )
    session.add(row)
    await session.flush()

    return Stored(
        object_id=row.id,
        storage_key=key,
        sha256=digest,
        size_bytes=len(content),
        content_type=content_type,
        backend=backend.name,
    )


async def read(session: AsyncSession, object_id: uuid.UUID) -> tuple[bytes, StoredObject]:
    """
    Fetch stored bytes, verifying they are the bytes that were stored.

    🔴 The hash check is not paranoia about the filesystem. It is what makes
    "the PDF you hold is the PDF you sent" a claim rather than a hope: if the
    object behind a key were ever replaced, every delivery record referencing
    it would silently start describing a different document.
    """
    row = await session.scalar(select(StoredObject).where(StoredObject.id == object_id))
    if row is None or row.is_deleted:
        raise StorageError("No such stored object.")

    content = get_backend().get(row.storage_key)
    if sha256_bytes(content) != row.sha256:
        raise StorageError(
            f"Stored object {object_id} does not match its recorded hash. "
            "The file behind this key has changed; treat any document that "
            "references it as unverified."
        )
    return content, row
