"""Driver analysis ("why did it change?") and automatic insights, computed only from approved metrics.

Everything here is arithmetic on the same governed query path as every other answer (run_plan): no model decides
numbers. The output is a decomposition, not an explanation of cause, and says so. A month that may still be
incomplete is excluded and named, history that is too short is refused, and numbers that do not reconcile are
disclosed.
"""
import statistics
from calendar import monthrange
from datetime import date

from sqlalchemy.orm import Session

from ..deps import Principal
from .common import run_plan
from .query_engine import Plan, QueryRejected

DIMENSIONS = ["product", "region", "customer", "category", "segment"]
TOP_DRIVERS = 5
ANOMALY_Z = 2.0
BASELINE_MONTHS = 6
MIN_BASELINE = 4


class NotEnoughData(Exception):
    """The question is valid but the data cannot support it; the message is shown to the person."""


def _ym(s: str) -> tuple[int, int]:
    return int(s[:4]), int(s[5:7])


def _month_range(first: str, last: str) -> list[str]:
    (y, m), (y2, m2) = _ym(first), _ym(last)
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _label(months: list[str]) -> str:
    return months[0] if len(months) == 1 else f"{months[0]} to {months[-1]}"


def _num(v) -> float:
    return float(v) if v is not None else 0.0


def complete_months(series: dict[str, float], latest: date | None) -> tuple[list[str], str | None]:
    """Calendar months from the first to the last month with data. The month of the latest order is excluded unless
    that order is on the last day of the month (otherwise the month may be partial). Returns (months, excluded)."""
    if not series:
        return [], None
    months = _month_range(min(series), max(series))
    excluded = None
    if latest is not None:
        ly, lm = latest.year, latest.month
        last = f"{ly:04d}-{lm:02d}"
        if months and months[-1] == last and latest.day < monthrange(ly, lm)[1]:
            excluded = months.pop()
    return months, excluded


def _series(db: Session, p: Principal, metric: str) -> tuple[dict[str, float], dict]:
    res = run_plan(db, p, Plan(intent="compare_metric_over_time", metric=metric, dimensions=["month"],
                               visualization="table"))
    return {str(r[0]): _num(r[1]) for r in res["rows"] if r[0] is not None}, res


def _anomaly(values: list[float], months: list[str]) -> dict | None:
    """Latest complete month against up to six before it; needs at least four to say anything."""
    if len(values) < MIN_BASELINE + 1:
        return None
    base = values[-1 - BASELINE_MONTHS:-1]
    if len(base) < MIN_BASELINE:
        return None
    sd = statistics.pstdev(base)
    mean = statistics.fmean(base)
    if sd == 0:
        return None
    z = (values[-1] - mean) / sd
    return {"month": months[-1], "value": round(values[-1], 2), "baseline_mean": round(mean, 2),
            "baseline_months": len(base), "z_score": round(z, 2), "flagged": abs(z) >= ANOMALY_Z}


def explain_change(db: Session, p: Principal, metric: str, n: int = 1) -> dict:
    """Compare the last `n` complete months with the `n` before them and attribute the change to each dimension."""
    if not 1 <= n <= 12:
        raise NotEnoughData("Choose a period between 1 and 12 months.")
    series, base_res = _series(db, p, metric)
    latest = None
    if base_res["freshness"]["latest_order_date"]:
        latest = date.fromisoformat(base_res["freshness"]["latest_order_date"])
    months, excluded = complete_months(series, latest)
    if len(months) < 2 * n:
        raise NotEnoughData(
            f"I need at least {2 * n} complete months of data to compare the last {n} month{'s' if n > 1 else ''} with the "
            f"{n} before; there {'is' if len(months) == 1 else 'are'} {len(months)}"
            + (f" ({excluded} is still in progress and is not used)." if excluded else "."))
    cur, prev = months[-n:], months[-2 * n:-n]
    values = [series.get(mo, 0.0) for mo in months]
    cur_total, prev_total = sum(series.get(mo, 0.0) for mo in cur), sum(series.get(mo, 0.0) for mo in prev)
    delta = cur_total - prev_total
    pct = None if prev_total == 0 else delta / abs(prev_total) * 100
    label = base_res["metric"]["label"]
    sqls = [base_res["sql"]]
    caveats = list(base_res["caveats"])
    caveats.append("This breaks the change down by group; it shows where the change happened, not why it happened. "
                   "Check the business reason before acting on it.")
    if excluded:
        caveats.append(f"{excluded} is still in progress (latest order {latest}) and is not included.")
    gaps = [mo for mo in months if mo not in series]
    if gaps:
        caveats.append("No data for " + ", ".join(gaps) + "; those months are counted as zero.")
    if len(months) < 6:
        caveats.append(f"Only {len(months)} complete months of history: treat patterns with caution.")

    drivers: dict[str, list[dict]] = {}
    truncated = False
    for dim in DIMENSIONS:
        try:
            res = run_plan(db, p, Plan(intent="compare_metric_over_time", metric=metric,
                                       dimensions=["month", dim], visualization="table"))
        except Exception:  # noqa: BLE001 - the dimension is simply not available for this organisation
            continue
        sqls.append(res["sql"])
        agg: dict[str, list[float]] = {}
        for mo, member, val in ((str(r[0]), r[1], _num(r[2])) for r in res["rows"]):
            name = "(blank)" if member in (None, "") else str(member)
            a = agg.setdefault(name, [0.0, 0.0])
            if mo in cur:
                a[0] += val
            elif mo in prev:
                a[1] += val
        if len(res["rows"]) >= 10_000:
            truncated = True
        rows = []
        for name, (c, pv) in agg.items():
            d = c - pv
            if abs(d) < 0.005 and c == 0 and pv == 0:
                continue
            rows.append({"member": name, "current": round(c, 2), "previous": round(pv, 2), "change": round(d, 2),
                         "share_of_change": None if abs(delta) < 0.005 else round(d / delta * 100, 1),
                         "status": "new" if pv == 0 and c != 0 else "stopped" if c == 0 and pv != 0 else "changed"})
        rows.sort(key=lambda r: abs(r["change"]), reverse=True)
        gap = round(delta - sum(r["change"] for r in rows), 2)
        if abs(gap) > 0.01:
            caveats.append(f"By {dim}, the groups add up to a change that differs from the total by {gap}; "
                           "some rows may not be assigned to a group.")
        if rows:
            drivers[dim] = rows[:TOP_DRIVERS]
    if truncated:
        caveats.append("Some breakdowns hit the result limit and may be incomplete.")
    if not drivers:
        caveats.append("No product, region or customer breakdown is available yet, so only the total change is shown.")

    direction = "was unchanged" if abs(delta) < 0.005 else ("rose" if delta > 0 else "fell")
    amount = "" if direction == "was unchanged" else f" by {abs(delta):,.2f}"
    pct_txt = "" if pct is None or direction == "was unchanged" else f" ({abs(pct):.1f}%)"
    headline = (f"{label} {direction}{amount}{pct_txt} in {_label(cur)} compared with {_label(prev)} "
                f"({cur_total:,.2f} vs {prev_total:,.2f}).")
    top = None
    for dim, rows in drivers.items():
        if rows and (top is None or abs(rows[0]["change"]) > abs(top[1]["change"])):
            top = (dim, rows[0])
    if top and abs(delta) >= 0.005:
        dim, r = top
        headline += f" The largest single change was {dim} '{r['member']}' ({r['change']:+,.2f})."

    return {"metric": base_res["metric"], "n_months": n,
            "period": {"current": cur, "previous": prev, "excluded_in_progress": excluded},
            "total": {"current": round(cur_total, 2), "previous": round(prev_total, 2), "change": round(delta, 2),
                      "percent": None if pct is None else round(pct, 1)},
            "drivers": drivers, "anomaly": _anomaly(values, months), "headline": headline, "caveats": caveats,
            "freshness": base_res["freshness"], "sql": sqls}


def overview(db: Session, p: Principal, metrics: list[str]) -> list[dict]:
    """One short insight per approved metric for the Home page: month-over-month change and any anomaly."""
    out = []
    for name in metrics:
        try:
            e = explain_change(db, p, name, 1)
        except (NotEnoughData, QueryRejected):
            continue
        except Exception:  # noqa: BLE001 - one metric with no usable data must not hide the others
            continue
        top = None
        for dim, rows in e["drivers"].items():
            if rows and (top is None or abs(rows[0]["change"]) > abs(top["change"])):
                top = {"dimension": dim, **rows[0]}
        out.append({"metric": e["metric"]["name"], "label": e["metric"]["label"], "headline": e["headline"],
                    "total": e["total"], "top_driver": top, "anomaly": e["anomaly"] if e["anomaly"] and e["anomaly"]["flagged"] else None,
                    "period": e["period"], "caveats": e["caveats"][:3]})
    return out
