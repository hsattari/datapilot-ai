"""Measure how well the planner turns questions into plans. Run from the repo root.

    python scripts/eval_planner.py                 # live Azure OpenAI model, settings from .env (needs AZURE_OPENAI_* values)
    python scripts/eval_planner.py --rules         # baseline: the built-in rule planner only (no model, no .env)
    python scripts/eval_planner.py --repeat 3      # run every case 3 times to expose non-determinism
    python scripts/eval_planner.py --only margin_percent,forecast

What it checks per case (tests/ai_evaluation/planner_cases.json): the outcome (plan / clarification / refusal), and for
plans the metric, dimensions, intent, time window and filters. It prints which planner answered (llm or rules) and, if
the model was configured but the answer came from the rules, the reason (exception type only). Nothing is executed
against data and no secrets are printed. Exit code 1 if any case fails or the live model was not actually used.
"""
import argparse
import json
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

ap = argparse.ArgumentParser()
ap.add_argument("--rules", action="store_true", help="ignore Azure settings and use the rule planner only")
ap.add_argument("--repeat", type=int, default=1)
ap.add_argument("--only", default="")
ap.add_argument("--cases", default=str(ROOT / "tests" / "ai_evaluation" / "planner_cases.json"))
args = ap.parse_args()

from app.config import get_settings  # noqa: E402  (reads .env from the current directory)
from app.services import planner  # noqa: E402
from app.services.semantic import DEFAULT_METRICS  # noqa: E402

s = get_settings()
if args.rules:
    s.azure_openai_endpoint = s.azure_openai_api_key = s.azure_openai_deployment = ""
live = bool(s.azure_openai_endpoint and s.azure_openai_api_key and s.azure_openai_deployment)
if live:
    print(f"Mode: LIVE model, deployment {s.azure_openai_deployment!r} at {s.azure_openai_endpoint.split('//')[-1].split('/')[0]}")
else:
    print("Mode: RULE PLANNER only" + ("" if args.rules else "  (AZURE_OPENAI_* missing in .env; run from the repo root)"))

DESC = {m["name"]: m["description"] for m in DEFAULT_METRICS}
cases = json.loads(pathlib.Path(args.cases).read_text())
if args.only:
    want = set(args.only.split(","))
    cases = [c for c in cases if c["id"] in want]


def judge(case, res):
    """Return a list of problems (empty = pass)."""
    expect = case["expect"] if isinstance(case["expect"], list) else [case["expect"]]
    if res.outcome not in expect:
        return [f"outcome {res.outcome!r}, wanted {'/'.join(expect)}"]
    if res.outcome != "plan":
        return []
    p, bad = res.plan, []
    if "metric" in case and p.metric != case["metric"]:
        bad.append(f"metric {p.metric!r}, wanted {case['metric']!r}")
    if "dimensions" in case and set(p.dimensions) != set(case["dimensions"]):
        bad.append(f"dimensions {sorted(p.dimensions)}, wanted {sorted(case['dimensions'])}")
    if "intent" in case and p.intent != case["intent"]:
        bad.append(f"intent {p.intent!r}, wanted {case['intent']!r}")
    if "months" in case and not (p.time_range.type == "last_n_complete_months" and p.time_range.n == case["months"]):
        bad.append(f"time_range {p.time_range.type}/{p.time_range.n}, wanted last {case['months']} months")
    if "filters" in case:
        got = sorted((f.field, f.value.lower()) for f in p.filters)
        want = sorted((f["field"], f["value"].lower()) for f in case["filters"])
        if got != want:
            bad.append(f"filters {got}, wanted {want}")
    elif p.filters and "metric" in case and case["id"] != "inject_in_data":
        bad.append(f"unexpected filters {[(f.field, f.value) for f in p.filters]}")
    return bad


results, sources, reasons = [], Counter(), Counter()
for case in cases:
    unapproved = {n: DESC[n] for n in case.get("unapproved", [])}
    approved = {n: d for n, d in DESC.items() if n not in unapproved}
    outcomes = []
    for _ in range(args.repeat):
        res = planner.make_plan(case["question"], approved, unapproved)
        problems = judge(case, res)
        sources[res.source] += 1
        if res.fallback_reason and res.fallback_reason != "not_configured":
            reasons[res.fallback_reason] += 1
        outcomes.append((res, problems))
    ok = all(not pr for _, pr in outcomes)
    results.append((case, ok))
    res, problems = next(((r, pr) for r, pr in outcomes if pr), outcomes[0])
    summary = (f"{res.plan.metric} {res.plan.dimensions or ''} {res.plan.time_range.type if res.plan.time_range.type != 'all_time' else ''}"
               if res.outcome == "plan" else res.outcome)
    print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<24} [{res.source}] {summary}")
    for pr in problems:
        print(f"        - {pr}")

passed = sum(ok for _, ok in results)
print(f"\n{passed}/{len(results)} cases passed  (planner used: {dict(sources)})")
rc = 0
if passed != len(results):
    rc = 1
    print("Failed:", ", ".join(c["id"] for c, ok in results if not ok))
filtered = reasons.pop("content_filter", 0)
if filtered:
    print(f"Note: Azure's content filter blocked {filtered} prompt(s) before the model answered; the rule planner handled them "
          "(expected for prompt-injection cases, and safe).")
if live and reasons:
    rc = 1
    print(f"WARNING: the live model was configured but failed {sum(reasons.values())} time(s) and the rule planner answered. "
          f"Reasons: {dict(reasons)}")
    print("  JSONDecodeError/invalid_plan = the model replied with something unusable; AuthenticationError/NotFoundError/"
          "PermissionDeniedError = key, endpoint or deployment name; APITimeoutError/APIConnectionError = network; "
          "BadRequestError = the request itself was refused (check the deployment supports JSON mode and the API version).")
print("(Cases answered by 'rules' without a reason are the hard policy checks that run before the model on purpose.)")
sys.exit(rc)
