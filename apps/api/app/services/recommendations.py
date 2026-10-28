"""Written recommendations: what to look at next, with the evidence, the baseline, how sure we are and the caveats.

Deterministic. Every number comes from `insights.explain_change` (the governed query path) and `health.issues`; the
wording is fixed templates, so no model can invent a figure. A recommendation is a prompt to check something, never a
claim about cause: the breakdown shows where a change happened, not why. Confidence is a count of stated reasons.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..deps import Principal
from . import health, insights

MATERIAL_PCT = 5.0        # smaller month-on-month moves are noise for a recommendation
CONCENTRATED = 50.0       # one group explains at least this share of the change
MAX_RECOMMENDATIONS = 12
NOT_CAUSE = "This shows where the numbers moved, not why. Confirm the business reason before acting."


def _level(points: int) -> str:
    return "high" if points >= 4 else "medium" if points == 3 else "low"


def _confidence(e: dict, top_share: float | None, data_problem: bool) -> dict:
    reasons, pts = [], 0
    short = any(c.startswith("Only ") for c in e["caveats"])
    gaps = any("differs from the total" in c or "hit the result limit" in c or "counted as zero" in c for c in e["caveats"])
    for ok, good, bad in ((not short, "at least six complete months of history", "fewer than six complete months of history"),
                          (not gaps, "the breakdown adds up to the total", "the breakdown does not fully add up to the total"),
                          (not data_problem, "no failed refresh or missing file", "a data source has an open problem"),
                          (top_share is not None and top_share >= CONCENTRATED, "one group explains most of the change",
                           "the change is spread across groups, or no breakdown is available")):
        pts += ok
        reasons.append(("+ " if ok else "- ") + (good if ok else bad))
    return {"level": _level(pts), "reasons": reasons}


def _top(e: dict) -> tuple[str, dict] | None:
    best = None
    for dim, rows in e["drivers"].items():
        if rows and (best is None or abs(rows[0]["change"]) > abs(best[1]["change"])):
            best = (dim, rows[0])
    return best


def for_metric(e: dict, data_problem: bool) -> list[dict]:
    label, name, tot, per = e["metric"]["label"], e["metric"]["name"], e["total"], e["period"]
    cur, prev = ", ".join(per["current"]), ", ".join(per["previous"])
    baseline = {"period": prev, "value": tot["previous"], "current_period": cur, "current_value": tot["current"]}
    out: list[dict] = []
    pct, delta = tot["percent"], tot["change"]
    material = abs(delta) >= 0.005 and (pct is None or abs(pct) >= MATERIAL_PCT)
    top = _top(e)
    share = top[1]["share_of_change"] if top else None
    base = {"metric": name, "baseline": baseline, "sql": e["sql"], "freshness": e["freshness"]}

    if material:
        fell = delta < 0
        ev = [f"{label} was {tot['current']:,.2f} in {cur} against {tot['previous']:,.2f} in {prev} "
              f"({delta:+,.2f}{'' if pct is None else f', {pct:+.1f}%'})."]
        steps = []
        if top and share is not None and share >= CONCENTRATED:
            dim, r = top
            ev.append(f"{dim.capitalize()} '{r['member']}' accounts for {share:.0f}% of the change "
                      f"({r['previous']:,.2f} to {r['current']:,.2f}).")
            steps.append(f"Compare {dim} '{r['member']}' between {prev} and {cur}: orders, prices, quantities.")
            steps.append(f"Ask whoever looks after {dim} '{r['member']}' whether something changed "
                         f"(stock, price, a lost or won {'customer' if dim == 'customer' else 'deal'}, a promotion, the season).")
        else:
            steps.append("No single group explains most of the change; open the breakdowns and look at the largest few together.")
        if not fell and share is not None and share >= 60:
            steps.append("Most of the growth sits in one group: check you are not relying on it, and whether it will repeat.")
        out.append({**base, "id": f"{name}:{'fall' if fell else 'rise'}", "kind": "fall" if fell else "rise",
                    "priority": 1 if fell else 3,
                    "title": f"{'Look into the fall' if fell else 'Understand the rise'} in {label}",
                    "evidence": ev, "next_steps": steps, "confidence": _confidence(e, share, data_problem),
                    "caveats": [*e["caveats"][:4], NOT_CAUSE]})
    stopped = [(d, r) for d, rows in e["drivers"].items() for r in rows if r["status"] == "stopped"]
    for d, r in stopped[:2]:
        out.append({**base, "id": f"{name}:stopped:{d}:{r['member']}", "kind": "stopped", "priority": 2,
                    "title": f"{d.capitalize()} '{r['member']}' has no {label} this period",
                    "evidence": [f"{d.capitalize()} '{r['member']}' contributed {r['previous']:,.2f} in {prev} and nothing in {cur}."],
                    "next_steps": [f"Check whether '{r['member']}' was discontinued, renamed, or simply not yet uploaded.",
                                   "If it is a renamed item, fix the mapping so history and new sales line up."],
                    "confidence": _confidence(e, r["share_of_change"], data_problem),
                    "caveats": [*e["caveats"][:3], NOT_CAUSE]})
    a = e["anomaly"]
    if a and a["flagged"]:
        out.append({**base, "id": f"{name}:anomaly", "kind": "anomaly", "priority": 2,
                    "title": f"{label} in {a['month']} is unusual",
                    "evidence": [f"{a['value']:,.2f} against an average of {a['baseline_mean']:,.2f} over the previous "
                                 f"{a['baseline_months']} months ({a['z_score']:+.1f} standard deviations)."],
                    "next_steps": [f"Rule out a data problem first: duplicate or missing uploads for {a['month']}.",
                                   "Then look for a one-off event (large order, refund batch, promotion)."],
                    "confidence": _confidence(e, share, data_problem),
                    "caveats": ["A statistical flag is a reason to look, not proof of a problem.", *e["caveats"][:3]]})
    return out


def build(db: Session, p: Principal) -> dict:
    """Recommendations for the organisation, most urgent first. Empty (with the reason) when there is nothing to say."""
    issues = health.issues(db, p.tenant_id)
    serious = [i for i in issues if i["severity"] in ("high", "medium")]
    names = [x.name for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id, m.Metric.status == "approved")
                                        .order_by(m.Metric.name))]
    recs: list[dict] = []
    if serious:
        recs.append({"id": "data:health", "kind": "data_problem", "priority": 0, "metric": None, "baseline": None,
                     "title": "Fix the data problems before relying on the numbers",
                     "evidence": [i["message"] for i in serious[:5]],
                     "next_steps": ["Open Data Sources, re-upload or correct the files named above.",
                                    "Re-read this page afterwards: the recommendations below were built from the data as it is now."],
                     "confidence": {"level": "high", "reasons": ["+ read directly from upload and schema records"]},
                     "caveats": [], "sql": [], "freshness": None})
    skipped = []
    for n in names:
        try:
            e = insights.explain_change(db, p, n, 1)
        except Exception as exc:  # noqa: BLE001 - one metric without enough data must not hide the others
            skipped.append({"metric": n, "reason": str(exc)})
            continue
        recs += for_metric(e, bool(serious))
    recs.sort(key=lambda r: (r["priority"], r["id"]))
    return {"recommendations": recs[:MAX_RECOMMENDATIONS], "skipped": skipped,
            "note": "Recommendations are checks to make, built from approved metrics. They are not predictions and do not say why."}
