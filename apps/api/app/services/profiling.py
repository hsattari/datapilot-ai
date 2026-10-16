"""File parsing, profiling and canonical mapping suggestions."""
import csv
import io
import json
import re
from pathlib import Path

import pandas as pd

CANONICAL_SYNONYMS = {
    "order_id": ["order_id", "orderid", "order id", "order", "order_no", "order number"],
    "order_date": ["order_date", "order date", "date", "ordered_at", "transaction_date"],
    "net_sales": ["net_sales", "net sales", "net_revenue", "revenue", "sales"],
    "cost_of_goods_sold": ["cost_of_goods_sold", "cogs", "cost", "unit_cost", "cost_price"],
    "product": ["product", "product_name", "sku", "item"],
    "region": ["region", "area", "territory", "country"],
    "customer": ["customer", "customer_id", "customer_name", "client"],
}
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


def suggest_mapping(profile: dict) -> dict:
    """Propose canonical mappings with confidence. Ambiguous names require clarification."""
    out: dict[str, dict] = {}
    names = [c["name"] for c in profile["columns"]]
    for canon, syns in CANONICAL_SYNONYMS.items():
        for n in names:
            if n in {slugify(x) for x in syns}:
                out[canon] = {"column": n, "confidence": 0.9 if n == canon else 0.75}
                break
    for n in names:
        if n in AMBIGUOUS and "net_sales" not in out:
            out["net_sales"] = {"column": n, "confidence": 0.4, "needs_clarification":
                                f"Does '{n}' mean gross sales, net sales or transaction value?"}
    return out
