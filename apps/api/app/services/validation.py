"""Upload validation: extension, signature, size, content. Macros are never executed."""
from pathlib import Path

ALLOWED = {".xlsx", ".csv", ".txt", ".json", ".jsonl", ".xml"}


class UploadRejected(ValueError):
    pass


def validate_upload(filename: str, data: bytes, max_mb: int) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise UploadRejected(f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED)}")
    if len(data) == 0:
        raise UploadRejected("Empty file")
    if len(data) > max_mb * 1024 * 1024:
        raise UploadRejected(f"File exceeds {max_mb} MB limit")
    if ext == ".xlsx":
        if not data.startswith(b"PK\x03\x04"):
            raise UploadRejected("File content does not match .xlsx signature")
        # Reject macro-enabled content disguised as xlsx.
        if b"vbaProject.bin" in data:
            raise UploadRejected("Macro-enabled workbooks are not accepted")
    else:
        if b"\x00" in data[:8192]:
            raise UploadRejected("Binary content detected in text file")
    if ext == ".xml":
        head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
        if not head.startswith(b"<"):
            raise UploadRejected("File content does not look like XML")
        # Entity declarations are how XML bombs and external-file reads work; data files never need them.
        low = data.lower()
        if b"<!doctype" in low or b"<!entity" in low:
            raise UploadRejected("XML with a DOCTYPE or entity declarations is not accepted")
    return ext
