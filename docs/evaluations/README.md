# Planner evaluation records

One JSON file per recorded run of `scripts/eval_planner.py --record docs/evaluations`, named
`<UTC time>-<git commit>-<rules|live>.json`. Each holds the commit, mode, deployment name (never a key), runs per case,
pass/fail per case, which planner answered, and latency (median and slowest, ms). A new run is compared with the newest
earlier file of the same mode and the script exits 1 on a regression (a case that passed before and fails now).

- **rules** files: the built-in rule planner only. It is expected to miss a few phrasings the model handles
  (`sold_in_total`, `filter_and_window` at the time of the first record), so a rules baseline below 100% is not a failure.
- **live** files: the Azure OpenAI planner. Only the project owner can produce these (needs the key in `.env`):
  `python scripts/eval_planner.py --record docs/evaluations --repeat 3`. Commit the file with the release it measured.
- Not measured: token counts and cost (the planner does not report them).
