"""Show which database DATABASE_URL points at, the migration revision, tables and extensions.
Prints no password. Run from the repo root or apps/api: python scripts/check_db.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
from sqlalchemy import create_engine, text
from app.config import get_settings

url = get_settings().database_url
e = create_engine(url)
print("database:", e.url.render_as_string(hide_password=True))
with e.connect() as c:
    if e.dialect.name != "postgresql":
        print("NOT PostgreSQL - DATABASE_URL is not set to Azure in this window")
    print("revision:", c.execute(text("select version_num from alembic_version")).scalar())
    tables = [r[0] for r in c.execute(text(
        "select table_name from information_schema.tables where table_schema='public' order by 1"))]
    print(len(tables), "tables:", ", ".join(tables))
    if e.dialect.name == "postgresql":
        print("extensions:", [r[0] for r in c.execute(text("select extname from pg_extension order by 1"))])
