"""Export the governed metric registry as a Power BI / Fabric semantic model starting point.

Only APPROVED metrics are exported, each with its DAX measure. Critical detail: SQL aggregates skip a row when
any operand is NULL, but DAX treats BLANK as 0 in arithmetic. So multi-column measures are wrapped with
FILTER(... NOT ISBLANK ...) to reproduce the SQL semantics, and distinct counts use DISTINCTCOUNTNOBLANK.
The DAX is generated, NOT executed here: validate it in Power BI against the app's numbers (reference: 140).
"""
import json

import sqlglot
from sqlglot import exp

TABLE = "fact_sales"
COLUMNS = [("order_id", "string"), ("order_date", "dateTime"), ("net_sales", "decimal"),
           ("cost_of_goods_sold", "decimal"), ("product", "string"), ("region", "string"), ("customer", "string")]
_COLNAMES = {c for c, _ in COLUMNS}


class NotExportable(ValueError):
    pass


def _ref(col: str) -> str:
    if col not in _COLNAMES:
        raise NotExportable(f"Unknown column {col}")
    return f"{TABLE}[{col}]"


def _to_dax(node: exp.Expression) -> str:
    if isinstance(node, exp.Column):
        return _ref(node.name)
    if isinstance(node, exp.Literal):
        return node.this
    if isinstance(node, exp.Paren):
        return f"({_to_dax(node.this)})"
    ops = {exp.Add: "+", exp.Sub: "-", exp.Mul: "*"}
    for cls, sym in ops.items():
        if isinstance(node, cls):
            return f"{_to_dax(node.left)} {sym} {_to_dax(node.right)}"
    raise NotExportable(f"Unsupported construct: {type(node).__name__}")


def measure_dax(expression: str, aggregation: str) -> str:
    tree = sqlglot.parse_one(expression, dialect="duckdb")
    cols = sorted({c.name for c in tree.find_all(exp.Column)})
    if not cols:
        raise NotExportable("Expression references no columns")
    dax = _to_dax(tree)
    guard = " && ".join(f"NOT ISBLANK({_ref(c)})" for c in cols)
    if aggregation == "count_distinct":
        if not isinstance(tree, exp.Column):
            raise NotExportable("count_distinct supports a single column")
        return f"DISTINCTCOUNTNOBLANK({_ref(cols[0])})"
    single = isinstance(tree, exp.Column)
    if aggregation == "sum":
        return f"SUM({_ref(cols[0])})" if single else f"SUMX(FILTER({TABLE}, {guard}), {dax})"
    if aggregation == "avg":
        return f"AVERAGE({_ref(cols[0])})" if single else f"AVERAGEX(FILTER({TABLE}, {guard}), {dax})"
    raise NotExportable(f"Unsupported aggregation {aggregation}")


def build_export(metrics: list[dict]) -> dict:
    measures, skipped = [], []
    for m in metrics:
        if m["status"] != "approved":
            continue
        try:
            dax = measure_dax(m["expression"], m["aggregation"])
        except (NotExportable, sqlglot.errors.ParseError) as e:
            skipped.append({"metric": m["name"], "reason": str(e)})
            continue
        measures.append({"name": m["label"], "expression": dax, "description": m["description"],
                         "formatString": "#,0.00", "annotations": [
                             {"name": "datapilot_metric", "value": m["name"]},
                             {"name": "datapilot_version", "value": str(m["version"])}]})
    model = {
        "name": "DataPilot",
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-GB",
            "tables": [{
                "name": TABLE,
                "columns": [{"name": c, "dataType": t, "sourceColumn": c,
                             **({"summarizeBy": "none"} if t != "decimal" else {"summarizeBy": "none"})}
                            for c, t in COLUMNS],
                "measures": measures,
                # TEMPLATE: bind this partition to your source (Databricks SQL / Fabric Lakehouse / Warehouse)
                # in Power BI Desktop or Tabular Editor. Not auto-bound because it needs your workspace details.
                "partitions": [{"name": TABLE, "mode": "import", "source": {
                    "type": "m", "expression": ["let", "    Source = #table(type table [order_id = text], {})",
                                               "in", "    Source"]}}],
            }],
        },
    }
    measures_dax = "\n\n".join(f"{m['name']} = {m['expression']}" for m in measures)
    return {"model_bim": model, "measures_dax": measures_dax, "skipped": skipped,
            "notes": ["Measures are generated, not executed here. Compare with the app (reference gross profit = 140).",
                      "Bind the partition to your data source before refresh.",
                      "Add a Date table and relate it to fact_sales[order_date] for month/time analysis."]}
