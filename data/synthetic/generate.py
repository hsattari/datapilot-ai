"""Generate synthetic retail data (deterministic). Usage: python generate.py [outdir]"""
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)

# Reference dataset from the brief: gross profit must equal 140.
with open(out / "orders_reference.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "order_date", "product", "region", "customer", "net_sales", "cost"])
    w.writerows([["A", "2026-01-10", "Widget", "North", "C1", 100, 60],
                 ["B", "2026-01-20", "Gadget", "South", "C2", 200, 130],
                 ["C", "2026-02-05", "Widget", "North", "C1", 50, 20]])

rng = random.Random(42)
products = {"Widget": (20, 0.55), "Gadget": (45, 0.65), "Gizmo": (80, 0.5), "Doohickey": (15, 0.7)}
regions = ["North", "South", "East", "West"]
start = date(2025, 8, 1)
with open(out / "orders_retail.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "order_date", "product", "region", "customer", "net_sales", "cost"])
    for i in range(1, 801):
        d = start + timedelta(days=rng.randrange(0, 420))
        p = rng.choice(list(products))
        price, cost_ratio = products[p]
        qty = rng.randint(1, 5)
        sales = round(price * qty * rng.uniform(0.9, 1.1), 2)
        if rng.random() < 0.03:
            sales = -sales  # valid refund
        cost = "" if rng.random() < 0.02 else round(abs(sales) * cost_ratio, 2) * (1 if sales > 0 else -1)
        w.writerow([f"O{i:05d}", d.isoformat(), p, rng.choice(regions), f"C{rng.randint(1, 120):03d}", sales, cost])
print("written to", out)
