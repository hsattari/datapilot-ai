"""Check the Azure resources created by infrastructure/terraform. Run from the repo root after `terraform apply`:

    pip install -r apps/api/requirements.txt
    az login                                   (the signed-in user needs 'Storage Blob Data Contributor': Terraform grants it)
    python scripts/test_azure_resources.py

Needs in .env:
    DATABASE_URL=postgresql+psycopg2://datapilot:<password>@<server>.postgres.database.azure.com:5432/datapilot?sslmode=require
    ADLS_ACCOUNT_URL=https://<storageaccount>.dfs.core.windows.net      ADLS_FILESYSTEM=raw
(`terraform output` prints both values.) It checks: PostgreSQL connection over TLS, pgvector is allow-listed and works,
and the ADLS raw-storage backend (write, read back, immutability, delete). It leaves nothing behind and never prints
passwords. Exception text may show host names; do not paste it anywhere public.
"""
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.storage import AdlsStorage  # noqa: E402

s = get_settings()
ok_all = True


def step(name, fn):
    global ok_all
    try:
        detail = fn()
        print(f"PASS  {name}" + (f"  ({detail})" if detail else ""))
        return True
    except Exception as e:  # noqa: BLE001 - diagnostic script
        ok_all = False
        print(f"FAIL  {name}\n        {type(e).__name__}: {str(e).strip()[:300]}")
        return False


# ---------------------------------------------------------------- PostgreSQL
def pg():
    if not s.database_url.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL in .env is not a postgresql:// URL (it is still the local default?)")
    if "sslmode=require" not in s.database_url:
        raise RuntimeError("add ?sslmode=require to DATABASE_URL (Azure PostgreSQL requires TLS)")
    eng = create_engine(s.database_url, connect_args={"connect_timeout": 15})
    return eng


eng = None


def connect():
    global eng
    eng = pg()
    with eng.connect() as c:
        v = c.execute(text("select version()")).scalar()
        return v.split(",")[0]


def allowlist():
    with eng.connect() as c:
        allowed = c.execute(text("show azure.extensions")).scalar()
    if "vector" not in allowed.lower():
        raise RuntimeError(f"azure.extensions is {allowed!r}: 'vector' is not allow-listed (terraform apply the configuration resource)")
    return f"azure.extensions = {allowed}"


def vector():
    with eng.begin() as c:
        c.execute(text("create extension if not exists vector"))
        d = c.execute(text("select ('[1,2,3]'::vector <=> '[1,2,4]'::vector)")).scalar()
    return f"cosine distance {float(d):.4f}"


if step("PostgreSQL: connect over TLS", connect):
    step("PostgreSQL: pgvector allow-listed (azure.extensions)", allowlist)
    step("PostgreSQL: CREATE EXTENSION vector and a vector query", vector)
else:
    print("      (skipping the other PostgreSQL checks)")


# ---------------------------------------------------------------- ADLS Gen2
# One check per operation, so a failure says exactly which call the storage service refused.
store = None
base = f"selftest/{uuid.uuid4().hex}"
key, payload = f"{base}/hello.csv", b"order_id,net_sales\nA,1\n"


def adls_client():
    global store
    if not s.adls_account_url:
        raise RuntimeError("ADLS_ACCOUNT_URL is empty in .env (terraform output adls_account_url)")
    store = AdlsStorage(s.adls_account_url, s.adls_filesystem)
    return f"{s.adls_account_url.split('//')[-1]} / {s.adls_filesystem}"


def fs_exists():
    props = store.fs.get_file_system_properties()
    return f"file system '{props.name}' exists"


def write():
    store.put(key, payload)
    return key.split("/")[0] + "/..."


def read_back():
    if store.get(key) != payload:
        raise RuntimeError("read-back differs from what was written")


def overwrite_refused():
    try:
        store.put(key, b"changed")
    except Exception as e:  # noqa: BLE001
        return type(e).__name__
    raise RuntimeError("a raw object could be overwritten (raw files must be immutable)")


def delete():
    n = store.delete_prefix(base)
    if n != 1:
        raise RuntimeError(f"delete_prefix removed {n} files, expected 1")


if step("ADLS Gen2: sign in and open client", adls_client):
    if step("ADLS Gen2: file system exists", fs_exists):
        if step("ADLS Gen2: write a file (creates parent folders)", write):
            step("ADLS Gen2: read it back", read_back)
            step("ADLS Gen2: overwrite is refused (immutable raw data)", overwrite_refused)
            step("ADLS Gen2: delete the folder", delete)

print("\nALL PASSED" if ok_all else "\nSOME CHECKS FAILED")
sys.exit(0 if ok_all else 1)
