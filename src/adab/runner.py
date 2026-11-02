"""Run every task for every arm and record objective results."""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .agents import AgentRun, run_agent
from .backend import SqlBackend, SqlError, execute_script
from .checks import CheckContext, run_checks
from .config import Arm, Config
from .tasks import Task, Trap, render

SANDBOX_SCHEMA = "bench_work"

PREAMBLE = """You are working on an Amazon Redshift Serverless data warehouse.

- Region: {region}
- Workgroup: {workgroup}
- Database: {database}
- Source data: the TICKIT sample tables in schema `bench` (users, venue, category, date, event, listing, sales). Treat `bench` as read-only.
- Your sandbox: schema `bench_work`. Create anything you need there.
- S3 location you may write to: {s3_output}
- Use the AWS credentials already configured in your environment.

Run your SQL against the warehouse to confirm it works before you finish.
When you are done, save the final SQL that answers the task to a file named answer.sql
in the current directory. If the task asks for a query, answer.sql must end with that
single SELECT statement.

Task:
"""

AgentRunner = Callable[[Arm, str, Path, dict], AgentRun]


def _utc(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def reset_sandbox(backend: SqlBackend) -> None:
    backend.execute(f"DROP SCHEMA IF EXISTS {SANDBOX_SCHEMA} CASCADE")
    backend.execute(f"CREATE SCHEMA {SANDBOX_SCHEMA}")
    backend.execute(f"GRANT ALL ON SCHEMA {SANDBOX_SCHEMA} TO PUBLIC")


def agent_queries(backend: SqlBackend, start: dt.datetime, end: dt.datetime) -> list[tuple[str, str, str]]:
    """Statements the agent sent to Redshift in the time window: (status, error, text)."""
    sql = f"""
        SELECT status, COALESCE(error_message, ''), query_text
        FROM sys_query_history
        WHERE start_time BETWEEN '{_utc(start)}' AND '{_utc(end)}'
          AND query_text NOT LIKE '%adab-harness%'
          AND query_type <> 'UTILITY'
        ORDER BY start_time
    """
    try:
        res = backend.execute(sql)
    except SqlError:
        return []
    return [(str(r[0]).strip().lower(), str(r[1]).strip(), str(r[2] or "")) for r in res.rows]


def scan_traps(texts: list[str], traps: list[Trap]) -> list[str]:
    blob = "\n".join(texts)
    return [t.name for t in traps if t.pattern.search(blob)]


def run_benchmark(
    config: Config,
    tasks: list[Task],
    traps: list[Trap],
    backend: SqlBackend,
    arm_names: list[str],
    repeat: int = 1,
    s3_lister=None,
    agent_runner: AgentRunner | None = None,
    run_id: str | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    agent_runner = agent_runner or (lambda arm, prompt, wd, env: run_agent(arm, prompt, wd, env))
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(config.results_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {
        "run_id": run_id, "arms": [], "arm_commands": {}, "tasks": [], "repeat": repeat,
        "region": config.region, "workgroup": config.workgroup}
    for a in arm_names:  # a run can be built up arm by arm with the same --run-id
        if a not in meta["arms"]:
            meta["arms"].append(a)
        meta["arm_commands"][a] = config.arms[a].command
    meta["tasks"] = sorted(set(meta["tasks"]) | {t.id for t in tasks})
    meta_path.write_text(json.dumps(meta, indent=2))

    agent_env = {"AWS_REGION": config.region, "AWS_DEFAULT_REGION": config.region}
    if config.agent_profile:
        agent_env["AWS_PROFILE"] = config.agent_profile

    total = len(arm_names) * len(tasks) * repeat
    n = 0
    for arm_name in arm_names:
        arm = config.arms[arm_name]
        for task in tasks:
            for rep in range(1, repeat + 1):
                n += 1
                log(f"[{n}/{total}] {arm_name} · {task.id} · attempt {rep}")
                workdir = run_dir / arm_name / task.id / f"attempt-{rep}"
                workdir.mkdir(parents=True, exist_ok=True)
                values = {
                    "region": config.region,
                    "workgroup": config.workgroup,
                    "database": config.database,
                    "s3_output": config.s3_prefix("attempts", run_id, arm_name, task.id, f"attempt-{rep}"),
                }

                setup_error = ""
                try:
                    reset_sandbox(backend)
                    if task.setup_sql:
                        execute_script(backend, render(task.setup_sql, values))
                except SqlError as exc:
                    setup_error = str(exc)
                    log(f"    setup failed: {exc}")

                prompt = render(PREAMBLE, values) + render(task.prompt, values)
                (workdir / "PROMPT.md").write_text(prompt)
                start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=2)
                agent = agent_runner(arm, prompt, workdir, agent_env)
                end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=2)
                (workdir / "agent_stdout.txt").write_text(agent.stdout or "")
                (workdir / "agent_stderr.txt").write_text(agent.stderr or "")

                time.sleep(config.history_delay_s)  # sys_query_history is not instant
                queries = agent_queries(backend, start, end)
                failed = [q for q in queries if q[0] == "failed"]
                (workdir / "agent_queries.json").write_text(json.dumps(
                    [{"status": s, "error": e, "sql": q} for s, e, q in queries], indent=2))

                ctx = CheckContext(backend=backend, workdir=workdir, values=values, s3_lister=s3_lister)
                checks = run_checks(task, ctx) if not setup_error else []
                passed = bool(checks) and all(c.passed for c in checks)

                answer = ctx.answer_sql() or ""
                trap_hits = scan_traps([answer] + [q[2] for q in queries], traps)

                record = {
                    "run_id": run_id,
                    "arm": arm_name,
                    "task": task.id,
                    "category": task.category,
                    "attempt": rep,
                    "passed": passed,
                    "first_try": passed and not failed,
                    "failed_queries": len(failed),
                    "total_queries": len(queries),
                    "trap_hits": trap_hits,
                    "duration_s": agent.duration_s,
                    "timed_out": agent.timed_out,
                    "exit_code": agent.exit_code,
                    "agent_meta": agent.meta,
                    "setup_error": setup_error,
                    "checks": [asdict(c) for c in checks],
                    "first_errors": [e for _, e, _ in failed][:3],
                }
                with results_path.open("a") as fh:
                    fh.write(json.dumps(record) + "\n")
                status = "PASS" if passed else "FAIL"
                log(f"    {status} · failed queries: {len(failed)} · traps: {', '.join(trap_hits) or 'none'}")
    return run_dir
