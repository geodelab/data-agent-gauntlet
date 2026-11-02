# aws-data-agent-bench

**An open, repeatable benchmark for AI coding agents doing real data work on AWS.**

AI coding agents such as Claude Code, Kiro, Codex and Cursor now write SQL, design tables and debug warehouses. The [Agent Toolkit for AWS](https://github.com/aws/agent-toolkit-for-aws) gives them skills, like the Amazon Redshift `redshift-guide`, to do that better. But how much better, on which tasks, and where do they still go wrong?

`aws-data-agent-bench` (`adab`) answers that with your own account, your own agent and objective checks:

- **20 tasks on Amazon Redshift Serverless.** Query writing, schema design, loading, troubleshooting and optimization, on the public TICKIT sample dataset.
- **Pass/fail decided by the warehouse, not by the agent.** Every task has checks the harness runs itself: results compared with a reference query, catalog state, S3 objects.
- **A/B by design.** Run the same tasks as a `baseline` arm and a `skills` arm, for example without and with the `aws-data-analytics` plugin.
- **It measures the mistakes.** The harness reads `sys_query_history` and records every statement the agent sent: how many failed, and which PostgreSQL habits showed up (`string_agg`, `CREATE INDEX`, `SERIAL`, `stl_` views on Serverless…).
- **Safe by default.** A disposable sandbox stack, an agent IAM role scoped to one workgroup and one bucket, a daily compute cap, and a one-command teardown.

> Status: v0.1. The harness, tasks and checks are tested offline (`pytest`). Numbers you publish should come from your own runs. Please share them.

## How it works

```
             ┌──────────── adab run ────────────┐
 tasks/*.yaml│ 1. reset bench_work + task setup │      Amazon Redshift Serverless
 ───────────▶│ 2. send prompt to the agent ─────┼──▶  (agent role: one workgroup,
             │ 3. read sys_query_history         │      one bucket, nothing else)
             │ 4. run objective checks          │
             │ 5. write results.jsonl + summary │
             └──────────────────────────────────┘
```

Each attempt runs in its own working folder. The agent gets a short preamble (workgroup, schemas, S3 location) plus the task, works against the warehouse with its own tools, and saves its final SQL to `answer.sql`. The harness then decides the result.

## Metrics

| Metric | Meaning |
|---|---|
| **Pass rate** | All checks for the task passed |
| **First-try success** | Passed *and* the agent sent no failing statement to Redshift |
| **Failed queries** | Statements from the agent that Redshift rejected during the attempt |
| **PostgreSQL habits** | Patterns from `tasks/_traps.yaml` found in SQL the agent executed or saved |
| **Time / turns / cost** | Wall-clock time, plus turns and cost when the agent reports them (e.g. Claude Code JSON output) |

## Quick start

Requirements: Python 3.10+, AWS CLI v2, an AWS account where you can create IAM roles, and a coding agent CLI (Claude Code or Kiro CLI).

```bash
git clone https://github.com/geodelab/aws-data-agent-bench && cd aws-data-agent-bench
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

adab validate                                  # offline: checks the task files
adab setup --region us-east-1 --profile admin  # ~10 min: stack, sample data, adab.yaml
# add the printed [profile adab-agent] block to ~/.aws/config
adab doctor                                    # credentials, warehouse, agent CLI
```

Run both arms into one result set:

```bash
adab run --arm baseline --run-id main          # agent WITHOUT the plugin installed
# install the plugin, e.g. in Claude Code: /plugin install aws-data-analytics@claude-plugins-official
adab run --arm skills --run-id main            # same tasks WITH the plugin
adab report results/main                       # writes summary.md and attempts.csv
adab teardown --profile admin                  # delete everything
```

See [docs/running-the-benchmark.md](docs/running-the-benchmark.md) for the full guide, including Kiro, repeats and cost control.

## Tasks

| Category | Tasks | What they probe |
|---|---|---|
| query | 6 | `LISTAGG`, `QUALIFY`-style dedup, window ranking, `MEDIAN`, week truncation, pivots |
| schema | 4 | sort/dist keys instead of indexes, `IDENTITY` instead of `SERIAL`, `VARCHAR(n)` instead of `TEXT`, `DISTSTYLE ALL` |
| loading | 4 | `COPY` from S3, `UNLOAD` to Parquet, `MERGE` upserts, getting a generated id without `RETURNING` |
| troubleshooting | 4 | `SYS_` monitoring views on Serverless, table design from `SVV_TABLE_INFO`, leader-node functions |
| optimization | 2 | auto-refresh materialized views, approximate distinct counts |

Adding a task is one YAML file. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Cost and safety

- **Compute:** Redshift Serverless bills only while queries run. `adab setup` sets a daily usage limit (default 20 RPU-hours) after which the workgroup stops accepting queries.
- **Agent permissions:** the agent assumes `AgentRole`, which can run SQL on the benchmark workgroup, read the public sample bucket and use the benchmark output bucket. Nothing else.
- **Harness permissions:** the harness uses the namespace admin secret to reset the sandbox schema and read `sys_query_history`. It only executes the agent's `answer.sql` if it is a single read-only `SELECT`.
- **Unattended agents:** agents run with tool approval turned off. Use a sandbox AWS account.
- **Cleanup:** `adab teardown` deletes the stack, data, bucket and roles. S3 output also expires after 7 days.

Agent usage (model tokens) is billed by your agent provider and is not covered by the AWS cost above.

## Limitations

- One warehouse (Redshift Serverless) and one dataset (TICKIT) in v0.1. Athena, Glue and S3 Tables tasks are on the roadmap.
- LLM agents are non-deterministic. Use `--repeat 3` or more before drawing conclusions, and report the spread.
- Trap detection is pattern-based. A pattern in a failed exploratory query counts, even if the final answer is correct. That is intentional, because it measures wasted attempts.

## License

Apache-2.0. Not affiliated with or endorsed by Amazon Web Services. "AWS", "Amazon Redshift" and related marks belong to Amazon.com, Inc. or its affiliates.
