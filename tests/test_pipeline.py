"""End-to-end run with a fake warehouse and a fake agent (no AWS needed)."""

import json
from pathlib import Path

from adab.agents import AgentRun, build_command, _parse_meta
from adab.backend import QueryResult
from adab.config import Arm, Config
from adab.report import write_report
from adab.runner import run_benchmark
from adab.tasks import load_tasks, load_traps

TASKS = Path(__file__).resolve().parents[1] / "tasks"
ROWS = [("Austin", "Moody Theater, Zilker Park"), ("Boston", "TD Garden")]


class FakeWarehouse:
    """Answers any SELECT with ROWS, and reports one failed agent query."""

    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if "sys_query_history" in sql and "adab-harness" in sql:
            return QueryResult(["status", "error", "text"], [
                ("failed", "function string_agg does not exist", "SELECT string_agg(venuename, ', ') FROM bench.venue"),
                ("success", "", "SELECT LISTAGG(venuename, ', ') WITHIN GROUP (ORDER BY venuename) FROM bench.venue"),
            ])
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            return QueryResult(["venuecity", "venues"], list(ROWS))
        return QueryResult()


def fake_agent(arm, prompt, workdir, env):
    assert "bench_work" in prompt and "Task:" in prompt
    assert env["AWS_PROFILE"] == "adab-agent"
    (workdir / "answer.sql").write_text(
        "SELECT venuecity, LISTAGG(venuename, ', ') WITHIN GROUP (ORDER BY venuename) FROM bench.venue GROUP BY 1;")
    return AgentRun(exit_code=0, duration_s=1.5, timed_out=False, stdout='{"num_turns": 4, "total_cost_usd": 0.12}',
                    stderr="", meta={"num_turns": 4, "total_cost_usd": 0.12})


def make_config(tmp_path):
    return Config(region="us-east-1", workgroup="adab-wg", database="dev", admin_secret_arn="arn:secret",
                  output_bucket="bucket", arms={"baseline": Arm("baseline", ["claude", "-p", "{prompt}"]),
                                                 "skills": Arm("skills", ["claude", "-p", "{prompt}"])},
                  history_delay_s=0, results_dir=str(tmp_path / "results"))


def test_full_run_produces_scorecard(tmp_path):
    cfg = make_config(tmp_path)
    tasks = load_tasks(TASKS, ["query-listagg-venues"])
    run_dir = run_benchmark(cfg, tasks, load_traps(TASKS), FakeWarehouse(), ["baseline", "skills"],
                            agent_runner=fake_agent, run_id="test", log=lambda *_: None)
    records = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines()]
    assert len(records) == 2
    r = records[0]
    assert r["passed"] is True
    assert r["failed_queries"] == 1
    assert r["first_try"] is False
    assert r["trap_hits"] == ["string_agg"]
    assert (run_dir / "baseline" / "query-listagg-venues" / "attempt-1" / "PROMPT.md").exists()

    summary = write_report(run_dir).read_text()
    assert "| baseline | 100% (1/1) | 0% |" in summary
    assert "`string_agg`" in summary
    assert (run_dir / "attempts.csv").exists()


def test_missing_answer_fails(tmp_path):
    cfg = make_config(tmp_path)
    tasks = load_tasks(TASKS, ["query-listagg-venues"])

    def lazy_agent(arm, prompt, workdir, env):
        return AgentRun(0, 1.0, False, "", "")

    run_dir = run_benchmark(cfg, tasks, [], FakeWarehouse(), ["baseline"], agent_runner=lazy_agent,
                            run_id="lazy", log=lambda *_: None)
    r = json.loads((run_dir / "results.jsonl").read_text().splitlines()[0])
    assert r["passed"] is False
    assert "answer.sql was not written" in r["checks"][0]["detail"]


def test_unsafe_answer_is_not_executed(tmp_path):
    cfg = make_config(tmp_path)
    tasks = load_tasks(TASKS, ["query-listagg-venues"])
    wh = FakeWarehouse()

    def evil_agent(arm, prompt, workdir, env):
        (workdir / "answer.sql").write_text("DROP SCHEMA bench CASCADE;")
        return AgentRun(0, 1.0, False, "", "")

    run_benchmark(cfg, tasks, [], wh, ["baseline"], agent_runner=evil_agent, run_id="evil", log=lambda *_: None)
    assert not any("DROP SCHEMA bench " in s for s in wh.statements)


def test_build_command_and_meta():
    cmd, stdin = build_command(Arm("a", ["claude", "-p", "{prompt}"]), "hi")
    assert cmd == ["claude", "-p", "hi"] and stdin is None
    cmd, stdin = build_command(Arm("b", ["kiro-cli", "chat"]), "hi")
    assert cmd == ["kiro-cli", "chat"] and stdin == "hi"
    assert _parse_meta('noise\n{"num_turns": 3, "total_cost_usd": 0.5, "result": "x"}') == {
        "num_turns": 3, "total_cost_usd": 0.5}
