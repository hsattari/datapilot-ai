"""Raw object storage. Local filesystem for development; ADLS Gen2 for Azure deployments."""
import re
from pathlib import Path
from typing import Protocol

from ..config import get_settings


def safe_filename(name: str) -> str:
    name = Path(name).name  # strip any path components
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:200] or "file"


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("Invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            raise FileExistsError("Raw objects are immutable")
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class AdlsStorage:
    """ADLS Gen2 backend (managed identity / DefaultAzureCredential)."""

    def __init__(self, account_url: str, filesystem: str):
        from azure.identity import DefaultAzureCredential
        from azure.storage.filedatalake import DataLakeServiceClient

        svc = DataLakeServiceClient(account_url, credential=DefaultAzureCredential())
        self.fs = svc.get_file_system_client(filesystem)

    def put(self, key: str, data: bytes) -> None:
        self.fs.get_file_client(key).upload_data(data, overwrite=False)

    def get(self, key: str) -> bytes:
        return self.fs.get_file_client(key).download_file().readall()


def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "adls":
        return AdlsStorage(s.adls_account_url, s.adls_filesystem)
    return LocalStorage(s.local_storage_path)
