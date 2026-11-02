# Contributing

Thanks for helping make AI agents better at data work on AWS.

## Add a task

1. Create `tasks/<category>-<short-name>.yaml`. The file name must match `id`.
2. Use one of the categories: `query`, `schema`, `loading`, `troubleshooting`, `optimization`.
3. Write a prompt a data engineer would actually send. Be precise about outputs (column order, rounding, ties) so the check is fair.
4. Add at least one objective check:

| Check | Use it when |
|---|---|
| `result_match` | The agent writes a SELECT. The harness runs it and compares it with `reference_sql`. Options: `ordered`, `abs_tol`, `rel_tol` |
| `sql_match` | Compare a harness query (e.g. a view the agent created) with a reference |
| `scalar_equals` | One number proves success (`expected` or `expected_sql`) |
| `answer_executes` | Any valid answer is fine but it must run. Options: `min_columns`, `max_rows`, `expected_rows_sql`, `max_text_length` |
| `answer_contains` | The answer must use a feature (regex) |
| `file_equals_sql` | The agent reports a value in a file |
| `s3_objects` | The task produces files in S3 |

5. Use `setup_sql` to create task-specific tables in `bench_work`. The sandbox is dropped and recreated before every attempt. Placeholders available: `{region}`, `{workgroup}`, `{database}`, `{s3_output}`.
6. Run `adab validate` and `pytest`, then run your task once for real:

```bash
adab run --task <your-task-id> --arm baseline
```

## Add a trap

Add a pattern to `tasks/_traps.yaml` with a short description of the correct Redshift alternative, and add a positive and a negative example to `tests/test_tasks.py`.

## Share results

Open an issue titled `Results: <agent> <version> <date>` with your `summary.md`. Please include the agent, model, plugin versions and region.

## Ground rules

- No credentials, account ids or customer data in issues, results or tasks.
- Be fair to every tool and service. The goal is to improve agents, not to rank vendors.
- By contributing you agree your contribution is licensed under Apache-2.0.
