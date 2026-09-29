"""Upload validation: extension + magic bytes + size. Never trust the client-supplied content type."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath


class UploadRejected(Exception):
    pass


@dataclass(frozen=True)
class FileKind:
    kind: str  # pdf, xlsx, csv, image
    mime: str


ALLOWED_EXTENSIONS = {
    ".pdf": FileKind("pdf", "application/pdf"),
    ".xlsx": FileKind("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".csv": FileKind("csv", "text/csv"),
    ".txt": FileKind("csv", "text/csv"),
    ".png": FileKind("image", "image/png"),
    ".jpg": FileKind("image", "image/jpeg"),
    ".jpeg": FileKind("image", "image/jpeg"),
}


def safe_filename(name: str | None) -> str:
    base = PurePath((name or "document").replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ()]", "_", base).strip(" .")
    return (base or "document")[:200]


def validate_upload(filename: str | None, content: bytes, max_bytes: int) -> FileKind:
    if not content:
        raise UploadRejected("Leeg bestand.")
    if len(content) > max_bytes:
        raise UploadRejected(f"Bestand is groter dan {max_bytes // (1024 * 1024)} MB.")
    ext = PurePath(safe_filename(filename)).suffix.lower()
    kind = ALLOWED_EXTENSIONS.get(ext)
    if kind is None:
        raise UploadRejected("Bestandstype niet toegestaan. Toegestaan: PDF, XLSX, CSV, PNG, JPG.")
    if kind.kind == "pdf" and not content[:1024].lstrip().startswith(b"%PDF-"):
        raise UploadRejected("Bestand is geen geldige PDF.")
    if kind.kind == "xlsx":
        if not content.startswith(b"PK\x03\x04"):
            raise UploadRejected("Bestand is geen geldig Excel-bestand.")
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                names = zf.namelist()
                if "[Content_Types].xml" not in names or not any(n.startswith("xl/") for n in names):
                    raise UploadRejected("Bestand is geen geldig Excel-bestand.")
                if any(n.lower().endswith(".bin") and "vbaproject" in n.lower() for n in names):
                    raise UploadRejected("Excel-bestanden met macro's zijn niet toegestaan.")
                if sum(i.file_size for i in zf.infolist()) > max_bytes * 20:
                    raise UploadRejected("Excel-bestand is te groot na uitpakken.")
        except zipfile.BadZipFile as exc:
            raise UploadRejected("Bestand is geen geldig Excel-bestand.") from exc
    if kind.kind == "csv":
        if b"\x00" in content[:8192]:
            raise UploadRejected("CSV-bestand bevat binaire data.")
        try:
            content[:65536].decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                content[:65536].decode("cp1252")
            except UnicodeDecodeError as exc:
                raise UploadRejected("CSV-bestand heeft een onbekende tekencodering.") from exc
    if kind.kind == "image":
        png = content.startswith(b"\x89PNG\r\n\x1a\n")
        jpg = content.startswith(b"\xff\xd8\xff")
        if not (png or jpg):
            raise UploadRejected("Afbeelding is geen geldige PNG of JPG.")
    return kind
