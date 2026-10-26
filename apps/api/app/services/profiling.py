"""File parsing, profiling and canonical mapping suggestions."""
import csv
import io
import json
import re
from pathlib import Path

import pandas as pd

from .dimensions import DIMS

CANONICAL_SYNONYMS = {
    "order_id": ["order_id", "orderid", "order id", "order", "order_no", "order number"],
    "order_date": ["order_date", "order date", "date", "ordered_at", "transaction_date"],
    "net_sales": ["net_sales", "net sales", "net_revenue", "revenue", "sales"],
    "cost_of_goods_sold": ["cost_of_goods_sold", "cogs", "cost", "unit_cost", "cost_price"],
    "product": ["product", "product_name", "sku", "item"],
    "region": ["region", "area", "territory", "country"],
    "customer": ["customer", "customer_id", "customer_name", "client"],
}
# Looser matches on the words inside a column name (e.g. "purchase_cost", "total_turnover"). Used only when exactly one
# unused column fits and its data type is right, so a wrong guess is unlikely; ambiguous names stay with the person.
FUZZY_WORDS = {
    "net_sales": ({"revenue", "sales", "turnover", "income"}, "number"),
    "cost_of_goods_sold": ({"cost", "cogs", "purchase", "buying"}, "number"),
    "order_date": ({"date", "ordered", "day"}, "date"),
    "order_id": ({"order", "invoice", "receipt", "transaction"}, None),
    "product": ({"product", "item", "sku", "article"}, "text"),
    "region": ({"region", "area", "territory", "country", "city", "store", "branch"}, "text"),
    "customer": ({"customer", "client", "buyer"}, "text"),
}
ID_WORDS = {"id", "no", "num", "number", "ref", "code"}
# Names that could mean several things: never auto-accept without confirmation.
AMBIGUOUS = {"amount", "value", "total", "price"}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "col"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise ValueError("Cannot decode text")


def _read_delimited(text: str) -> pd.DataFrame:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        sep = dialect.delimiter
    except csv.Error:
        sep = ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False,
                       skipinitialspace=True, engine="python", on_bad_lines="skip")


def parse_file(filename: str, data: bytes, max_rows: int) -> dict[str, pd.DataFrame]:
    """Return {dataset_name: DataFrame of strings}. Raises ValueError on unparseable input."""
    ext = Path(filename).suffix.lower()
    stem = slugify(Path(filename).stem)
    frames: dict[str, pd.DataFrame] = {}
    if ext == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)  # cached values; no macros
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(c).strip() if c is not None else f"column_{i}" for i, c in enumerate(rows[0])]
            body = [["" if v is None else str(v) for v in r] for r in rows[1:max_rows + 1]]
            frames[f"{stem}_{slugify(ws.title)}" if len(wb.worksheets) > 1 else stem] = pd.DataFrame(body, columns=header)
    elif ext in (".csv", ".txt"):
        frames[stem] = _read_delimited(_decode(data))
    elif ext == ".json":
        obj = json.loads(_decode(data))
        frames[stem] = pd.json_normalize(obj if isinstance(obj, list) else [obj]).astype(str)
    elif ext == ".jsonl":
        recs = [json.loads(line) for line in _decode(data).splitlines() if line.strip()]
        frames[stem] = pd.json_normalize(recs).astype(str)
    else:
        raise ValueError(f"Unsupported extension {ext}")
    if not frames:
        raise ValueError("No tabular data found")
    for name, df in list(frames.items()):
        df.columns = [slugify(c) for c in df.columns]
        if len(df) > max_rows:
            raise ValueError("Row limit exceeded")
        if df.columns.duplicated().any():
            raise ValueError("Duplicate column names after normalisation")
    return frames


def infer_type(s: pd.Series) -> str:
    vals = s[s.astype(str).str.strip() != ""]
    if vals.empty:
        return "empty"
    if pd.to_numeric(vals, errors="coerce").notna().all():
        return "number"
    parsed = pd.to_datetime(vals, errors="coerce", format="mixed")
    if parsed.notna().mean() > 0.95:
        return "date"
    return "text"


def profile_frame(df: pd.DataFrame) -> dict:
    cols = []
    for c in df.columns:
        s = df[c]
        blank = s.astype(str).str.strip() == ""
        t = infer_type(s)
        info = {"name": c, "type": t, "null_count": int(blank.sum()),
                "distinct": int(s[~blank].nunique())}
        if t == "number":
            n = pd.to_numeric(s[~blank])
            info.update(min=float(n.min()), max=float(n.max()))
        elif t == "date":
            d = pd.to_datetime(s[~blank], errors="coerce", format="mixed")
            info.update(min=str(d.min().date()), max=str(d.max().date()))
        info["sample"] = [str(v) for v in s[~blank].head(3)]
        cols.append(info)
    return {"row_count": int(len(df)), "duplicate_rows": int(df.duplicated().sum()), "columns": cols}


def suggest_dimension(profile: dict, fact_like: bool) -> dict | None:
    """If a file looks like a products or customers list (a key column plus a category/segment column) and not
    like sales, propose it as a dimension. Never auto-applied: the user approves it."""
    if fact_like:
        return None
    names = [c["name"] for c in profile["columns"]]
    for role, d in DIMS.items():
        key = next((n for n in names if n in {slugify(x) for x in d["key_syn"]}), None)
        attr = next((n for n in names if n != key and n in {slugify(x) for x in d["attr_syn"]}), None)
        if key and attr:
            return {"role": role, "label": d["label"], "confidence": 0.85,
                    "mapping": {d["key_canon"]: key, d["attr_canon"]: attr}}
    return None


def suggest_mapping(profile: dict) -> dict:
    """Propose canonical mappings with confidence. Ambiguous names require clarification."""
    out: dict[str, dict] = {}
    names = [c["name"] for c in profile["columns"]]
    for canon, syns in CANONICAL_SYNONYMS.items():
        for n in names:
            if n in {slugify(x) for x in syns}:
                out[canon] = {"column": n, "confidence": 0.9 if n == canon else 0.75}
                break
    types = {c["name"]: c["type"] for c in profile["columns"]}
    for canon, (words, want) in FUZZY_WORDS.items():
        if canon in out:
            continue
        used = {v["column"] for v in out.values()}
        fits = []
        for n in names:
            toks = set(n.split("_"))
            if n in used or n in AMBIGUOUS or not toks & words:
                continue
            if want and types.get(n) != want:
                continue
            if canon == "order_id" and not toks & ID_WORDS:
                continue
            fits.append(n)
        if len(fits) == 1:
            out[canon] = {"column": fits[0], "confidence": 0.65}
    for n in names:
        if n in AMBIGUOUS and "net_sales" not in out:
            out["net_sales"] = {"column": n, "confidence": 0.4, "needs_clarification":
                                f"Does '{n}' mean gross sales, net sales or transaction value?"}
    return out
