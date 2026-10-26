"""Governed metric registry. A metric is never active until approved and its inputs exist."""
import sqlglot
from sqlglot import exp
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Metric

FACT_COLUMNS = {"order_id", "order_date", "net_sales", "cost_of_goods_sold",
                "product", "region", "customer"}
ALLOWED_AGGS = {"sum", "count_distinct", "avg"}
ALLOWED_EXPR_NODES = (exp.Column, exp.Sub, exp.Add, exp.Mul, exp.Paren, exp.Literal, exp.Identifier)

DEFAULT_METRICS = [
    dict(name="net_revenue", label="Net Revenue", description="Sum of net sales after discounts and refunds",
         expression="net_sales", aggregation="sum", status="approved"),
    dict(name="orders", label="Orders", description="Count of distinct orders",
         expression="order_id", aggregation="count_distinct", status="approved"),
    dict(name="gross_profit", label="Gross Profit", description="Net sales less recognised cost of goods sold",
         expression="net_sales - cost_of_goods_sold", aggregation="sum", status="requires_business_approval"),
]


class MetricInvalid(ValueError):
    pass


def validate_expression(expression: str) -> set[str]:
    """Only arithmetic over known fact columns. Returns the columns referenced."""
    try:
        tree = sqlglot.parse_one(expression, dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        raise MetricInvalid(f"Unparseable expression: {e}")
    cols = set()
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if not isinstance(n, ALLOWED_EXPR_NODES):
            raise MetricInvalid(f"Disallowed construct: {type(n).__name__}")
        if isinstance(n, exp.Column):
            if n.name not in FACT_COLUMNS:
                raise MetricInvalid(f"Unknown column: {n.name}")
            cols.add(n.name)
    return cols


def seed_metrics(db: Session, tenant_id: str) -> None:
    for m in DEFAULT_METRICS:
        db.add(Metric(tenant_id=tenant_id, model="fact_sales",
                      dimensions=["month", "product", "region", "customer"], **m))


def approve_metric(db: Session, tenant_id: str, name: str, has_cost: bool, commit: bool = True) -> Metric:
    m = db.scalar(select(Metric).where(Metric.tenant_id == tenant_id, Metric.name == name))
    if m is None:
        raise MetricInvalid("Metric not found")
    cols = validate_expression(m.expression)
    if "cost_of_goods_sold" in cols and not has_cost:
        raise MetricInvalid("Cannot approve: required source data (cost of goods sold) is not mapped")
    m.status = "approved"
    if commit:
        db.commit()
    return m
