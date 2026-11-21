"""Export the governed metric registry as a Power BI / Fabric semantic model starting point.

Only APPROVED metrics are exported, each with its DAX measure. Critical detail: SQL aggregates skip a row when
any operand is NULL, but DAX treats BLANK as 0 in arithmetic. So multi-column measures are wrapped with
FILTER(... NOT ISBLANK ...) to reproduce the SQL semantics, and distinct counts use DISTINCTCOUNTNOBLANK.
The DAX is generated, NOT executed here: validate it in Power BI against the app's numbers (reference: 140).
"""
import json

import sqlglot
from sqlglot import exp

from .dimensions import DIMS

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


_DIM_COLUMN = {"product": ("dim_product", "category"), "customer": ("dim_customer", "segment")}


def _dax_string(v: str) -> str:
    return '"' + str(v).replace('"', '""') + '"'


def _dax_date(iso: str) -> str:
    y, m_, d = (int(x) for x in iso.split("-"))
    return f"DATE({y}, {m_}, {d})"


def scope_filters(default_filters: list[dict] | None, effective_from: str | None, effective_to: str | None) -> list[str]:
    """CALCULATE filter arguments for a metric's scope (default filters and effective dates)."""
    out = []
    for f in default_filters or []:
        field = f["field"]
        if field in ("category",):
            out.append(f"dim_product[category] = {_dax_string(f['value'])}")
        elif field in ("segment",):
            out.append(f"dim_customer[segment] = {_dax_string(f['value'])}")
        elif field in _COLNAMES:
            out.append(f"{_ref(field)} = {_dax_string(f['value'])}")
        else:
            raise NotExportable(f"Unsupported scope field {field}")
    if effective_from:
        out.append(f"{TABLE}[order_date] >= {_dax_date(effective_from)}")
    if effective_to:
        out.append(f"{TABLE}[order_date] <= {_dax_date(effective_to)}")
    return out


def measure_dax(expression: str, aggregation: str, filters: list[str] | None = None) -> str:
    base = _measure_dax(expression, aggregation)
    return f"CALCULATE({base}, {', '.join(filters)})" if filters else base


def _measure_dax(expression: str, aggregation: str) -> str:
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
            dax = measure_dax(m["expression"], m["aggregation"],
                              scope_filters(m.get("default_filters"), m.get("effective_from"), m.get("effective_to")))
        except (NotExportable, sqlglot.errors.ParseError) as e:
            skipped.append({"metric": m["name"], "reason": str(e)})
            continue
        label = m["label"]
        ann = [{"name": "datapilot_metric", "value": m["name"]}, {"name": "datapilot_version", "value": str(m["version"])}]
        measures.append({"name": label, "expression": dax, "description": m["description"], "formatString": "#,0.00",
                         "annotations": ann})
        # Time intelligence over the date table: year to date, same period last year, and the change on last year.
        measures += [
            {"name": f"{label} YTD", "expression": f"TOTALYTD([{label}], dim_date[Date])", "formatString": "#,0.00",
             "annotations": ann + [{"name": "datapilot_kind", "value": "ytd"}]},
            {"name": f"{label} Prior Year", "expression": f"CALCULATE([{label}], SAMEPERIODLASTYEAR(dim_date[Date]))",
             "formatString": "#,0.00", "annotations": ann + [{"name": "datapilot_kind", "value": "prior_year"}]},
            {"name": f"{label} YoY %", "expression": f"DIVIDE([{label}] - [{label} Prior Year], [{label} Prior Year])",
             "formatString": "0.0%", "annotations": ann + [{"name": "datapilot_kind", "value": "yoy"}]}]
    def m_partition(name: str, cols: str) -> list[dict]:   # TEMPLATE: bind to your source before refresh
        return [{"name": name, "mode": "import", "source": {"type": "m", "expression": [
            "let", f"    Source = #table(type table [{cols}], {{}})", "in", "    Source"]}}]

    dims = [{"name": d["table"], "columns": [{"name": d["key_col"], "dataType": "string", "sourceColumn": d["key_col"], "summarizeBy": "none"},
                                           {"name": d["attr_col"], "dataType": "string", "sourceColumn": d["attr_col"], "summarizeBy": "none"}],
             "partitions": m_partition(d["table"], f"{d['key_col']} = text, {d['attr_col']} = text")} for d in DIMS.values()]
    date_table = {
        "name": "dim_date", "dataCategory": "Time",
        "columns": [{"name": "Date", "dataType": "dateTime", "isKey": True, "sourceColumn": "[Date]", "summarizeBy": "none"},
                    {"name": "Year", "dataType": "int64", "type": "calculated", "expression": "YEAR([Date])", "summarizeBy": "none"},
                    {"name": "Month number", "dataType": "int64", "type": "calculated", "expression": "MONTH([Date])", "summarizeBy": "none"},
                    {"name": "Month", "dataType": "string", "type": "calculated", "expression": 'FORMAT([Date], "yyyy-MM")',
                     "sortByColumn": "Month number", "summarizeBy": "none"}],
        "partitions": [{"name": "dim_date", "mode": "import", "source": {
            "type": "calculated",
            "expression": f"CALENDAR(MIN({TABLE}[order_date]), MAX({TABLE}[order_date]))"}}]}
    relationships = [{"name": "fact_date", "fromTable": TABLE, "fromColumn": "order_date", "toTable": "dim_date", "toColumn": "Date"}] + [
        {"name": f"fact_{d['table']}", "fromTable": TABLE, "fromColumn": d["fact_col"], "toTable": d["table"], "toColumn": d["key_col"]}
        for d in DIMS.values()]
    # TEMPLATE role for row-level security: copy per audience and set the value; assign people in the Power BI service.
    roles = [{"name": "Region (template)", "modelPermission": "read", "tablePermissions": [
        {"name": TABLE, "filterExpression": f'{TABLE}[region] = "REPLACE_WITH_REGION"'}]}]
    model = {
        "name": "DataPilot",
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-GB",
            "relationships": relationships,
            "roles": roles,
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
            }, date_table] + dims,
        },
    }
    measures_dax = "\n\n".join(f"{m['name']} = {m['expression']}" for m in measures)
    return {"model_bim": model, "measures_dax": measures_dax, "skipped": skipped,
            "notes": ["Measures are generated, not executed here. Compare with the app (reference gross profit = 140).",
                      "Bind the partition to your data source before refresh.",
                      "Included: a dim_date table (calendar from the first to the last order) related to fact_sales[order_date], "
                      "year-to-date, prior-year and year-on-year measures for each metric, dimension tables related on product and "
                      "customer, and a Region row-level-security role TEMPLATE (set the value and assign people in the Power BI service).",
                      "Metric scope (default filters and effective dates) is built into each measure with CALCULATE."]}
