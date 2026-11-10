"""Turn results.jsonl into a Markdown scorecard and CSV files."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


def load_results(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "results.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"No results.jsonl in {run_dir}")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _pct(num: float, den: float) -> str:
    return f"{100 * num / den:.0f}%" if den else "–"


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize(records: list[dict]) -> dict:
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_arm[r["arm"]].append(r)

    arms = {}
    for arm, rows in by_arm.items():
        costs = [r["agent_meta"].get("total_cost_usd") for r in rows if r.get("agent_meta", {}).get("total_cost_usd") is not None]
        arms[arm] = {
            "attempts": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "first_try": sum(r["first_try"] for r in rows),
            "avg_failed_queries": _mean([r["failed_queries"] for r in rows]),
            "avg_duration_s": _mean([r["duration_s"] for r in rows]),
            "timeouts": sum(r["timed_out"] for r in rows),
            "attempts_with_traps": sum(1 for r in rows if r["trap_hits"]),
            "total_cost_usd": sum(costs) if costs else None,
        }

    categories: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    traps: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tasks: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        categories[r["category"]][r["arm"]].append(r["passed"])
        for t in r["trap_hits"]:
            traps[t][r["arm"]] += 1
        tasks[r["task"]][r["arm"]].append(r)
    return {"arms": arms, "categories": categories, "traps": traps, "tasks": tasks}


def render_markdown(records: list[dict], run_meta: dict | None = None) -> str:
    s = summarize(records)
    arm_names = list(s["arms"])
    lines = ["# data-agent-gauntlet results", ""]
    if run_meta:
        lines += [f"Run `{run_meta.get('run_id', '')}` · region `{run_meta.get('region', '')}` · "
                  f"{len(run_meta.get('tasks', []))} tasks · {run_meta.get('repeat', 1)} attempt(s) per task", ""]

    lines += ["## Headline", "",
              "| Arm | Pass rate | First-try success | Avg failed queries | Avg time (s) | Attempts with PostgreSQL habits | Timeouts | Agent cost (USD) |",
              "|---|---|---|---|---|---|---|---|"]
    for arm in arm_names:
        a = s["arms"][arm]
        cost = f"{a['total_cost_usd']:.2f}" if a["total_cost_usd"] is not None else "n/a"
        lines.append(
            f"| {arm} | {_pct(a['passed'], a['attempts'])} ({a['passed']}/{a['attempts']}) | "
            f"{_pct(a['first_try'], a['attempts'])} | {a['avg_failed_queries']:.1f} | {a['avg_duration_s']:.0f} | "
            f"{a['attempts_with_traps']} | {a['timeouts']} | {cost} |")
    lines += ["", "*First-try success* = all checks passed **and** the agent sent no failing statement to Redshift.", ""]

    lines += ["## By category", "", "| Category | " + " | ".join(arm_names) + " |",
              "|---|" + "---|" * len(arm_names)]
    for cat in sorted(s["categories"]):
        cells = []
        for arm in arm_names:
            vals = s["categories"][cat].get(arm, [])
            cells.append(f"{_pct(sum(vals), len(vals))} ({sum(vals)}/{len(vals)})" if vals else "–")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")

    lines += ["", "## PostgreSQL habits seen in SQL sent to Redshift", "",
              "Counted once per attempt when the pattern appears in any statement the agent executed or in answer.sql.", ""]
    if s["traps"]:
        lines += ["| Habit | " + " | ".join(arm_names) + " |", "|---|" + "---|" * len(arm_names)]
        for trap in sorted(s["traps"]):
            lines.append(f"| `{trap}` | " + " | ".join(str(s["traps"][trap].get(arm, 0)) for arm in arm_names) + " |")
    else:
        lines.append("None detected.")

    lines += ["", "## Per task", "", "| Task | " + " | ".join(arm_names) + " |", "|---|" + "---|" * len(arm_names)]
    for task in sorted(s["tasks"]):
        cells = []
        for arm in arm_names:
            rows = s["tasks"][task].get(arm, [])
            if not rows:
                cells.append("–")
                continue
            ok = sum(r["passed"] for r in rows)
            fq = sum(r["failed_queries"] for r in rows)
            mark = "✅" if ok == len(rows) else ("⚠️" if ok else "❌")
            cells.append(f"{mark} {ok}/{len(rows)} · {fq} failed q")
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_report(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    records = load_results(run_dir)
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    out = run_dir / "summary.md"
    out.write_text(render_markdown(records, meta))

    with (run_dir / "attempts.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["arm", "task", "category", "attempt", "passed", "first_try", "failed_queries",
                    "total_queries", "duration_s", "timed_out", "trap_hits", "num_turns", "cost_usd", "failed_checks"])
        for r in records:
            meta_r = r.get("agent_meta", {})
            failed_checks = "; ".join(f"{c['name']}: {c['detail']}" for c in r["checks"] if not c["passed"])
            w.writerow([r["arm"], r["task"], r["category"], r["attempt"], r["passed"], r["first_try"],
                        r["failed_queries"], r["total_queries"], r["duration_s"], r["timed_out"],
                        "|".join(r["trap_hits"]), meta_r.get("num_turns", ""), meta_r.get("total_cost_usd", ""),
                        failed_checks or r.get("setup_error", "")])
    return out
