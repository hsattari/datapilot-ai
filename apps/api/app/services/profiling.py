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
    elif ext == ".xml":
        frames[stem] = parse_xml(data)
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


XML_MAX_DEPTH = 40
XML_MAX_ELEMENTS = 3_000_000
XML_MAX_COLUMNS = 500


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else "node"


def _flatten(el, prefix: str = "") -> dict[str, str]:
    """One record element -> flat columns. Attributes and single child elements become columns (nested names are
    joined with '_'); a child tag that repeats inside the record is kept whole as a compact JSON list so no data is
    dropped. The original file is stored unchanged, so the full structure is always recoverable."""
    out: dict[str, str] = {}
    for k, v in el.attrib.items():
        out[f"{prefix}{_local(k)}"] = v.strip()
    kids = list(el)
    if not kids:
        text = (el.text or "").strip()
        if text or not out:
            out[prefix.rstrip("_") or _local(el.tag)] = text
        return out
    groups: dict[str, list] = {}
    for c in kids:
        groups.setdefault(_local(c.tag), []).append(c)
    for tag, items in groups.items():
        if len(items) == 1:
            sub = _flatten(items[0], f"{prefix}{tag}_")
            for k, v in sub.items():
                out[k if k not in out else f"{k}_el"] = v
        else:
            lst = [_flatten(i) for i in items]
            out[f"{prefix}{tag}"] = json.dumps(lst if any(len(x) > 1 for x in lst) else [next(iter(x.values()), "") for x in lst],
                                               ensure_ascii=False)[:2000]
    text = (el.text or "").strip()
    if text:
        out.setdefault(f"{prefix}text".rstrip("_"), text)
    return out


def parse_xml(data: bytes) -> pd.DataFrame:
    """XML -> table using the first level that has a repeating element as the records. Hardened: DTDs, entities and
    external references are refused by the parser, and depth / element counts are capped."""
    from defusedxml import ElementTree as SafeET
    from defusedxml.common import DefusedXmlException

    try:
        root = SafeET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise ValueError("XML with a DOCTYPE, entities or external references is not accepted")
    except Exception as e:  # malformed XML: a permanent data error, not a retry
        raise ValueError(f"The XML is not well formed: {str(e)[:160]}")
    count, stack = 0, [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > XML_MAX_DEPTH or count > XML_MAX_ELEMENTS:
            raise ValueError("The XML is too deeply nested or too large")
        stack.extend((c, depth + 1) for c in node)
    level = [root]
    records = None
    for _ in range(XML_MAX_DEPTH):
        groups: dict[str, list] = {}
        for node in level:
            for c in node:
                groups.setdefault(_local(c.tag), []).append(c)
        repeated = [g for g in groups.values() if len(g) >= 2]
        if repeated:
            records = max(repeated, key=len)
            break
        nxt = [c for g in groups.values() for c in g]
        if not nxt:
            break
        level = nxt
    if records is None:
        records = [root]
    rows = [_flatten(r) for r in records]
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    if not cols:
        raise ValueError("No tabular data found in the XML")
    if len(cols) > XML_MAX_COLUMNS:
        raise ValueError(f"The XML flattens to {len(cols)} columns; the limit is {XML_MAX_COLUMNS}")
    return pd.DataFrame([{c: r.get(c, "") for c in cols} for r in rows], columns=cols).astype(str)


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
    # A bare key column ("id", "ref", "number") is an order key only when the file is clearly sales data already
    # (it has a sales amount AND a date) and exactly one such column exists; otherwise a products list would look like sales.
    if "order_id" not in out and "net_sales" in out and "order_date" in out:
        used = {v["column"] for v in out.values()}
        keys = [n for n in names if n in {"id", "key", "ref", "reference", "number", "no"} and n not in used]
        if len(keys) == 1:
            out["order_id"] = {"column": keys[0], "confidence": 0.6}
    for n in names:
        if n in AMBIGUOUS and "net_sales" not in out:
            out["net_sales"] = {"column": n, "confidence": 0.4, "needs_clarification":
                                f"Does '{n}' mean gross sales, net sales or transaction value?"}
    return out
