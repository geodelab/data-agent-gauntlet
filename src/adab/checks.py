"""Objective pass/fail checks run by the harness after the agent finishes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .backend import SqlBackend, SqlError
from .sqlutil import compare_results, is_read_only_query, normalize_value, split_statements, values_equal
from .tasks import Check, Task, render

ANSWER_FILE = "answer.sql"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class CheckContext:
    backend: SqlBackend
    workdir: Path
    values: dict[str, str]
    s3_lister: Callable[[str], list[str]] | None = None  # s3 uri prefix -> object keys

    def answer_sql(self) -> str | None:
        p = self.workdir / ANSWER_FILE
        return p.read_text() if p.exists() else None


def _answer_select(ctx: CheckContext) -> tuple[str | None, str]:
    """Return the agent's final SELECT, or (None, reason)."""
    text = ctx.answer_sql()
    if text is None or not text.strip():
        return None, f"{ANSWER_FILE} was not written"
    stmts = split_statements(text)
    if not stmts:
        return None, f"{ANSWER_FILE} is empty"
    last = stmts[-1]
    if not is_read_only_query(last):
        return None, f"the last statement in {ANSWER_FILE} is not a single read-only SELECT"
    return last, ""


def _run(ctx: CheckContext, sql: str):
    return ctx.backend.execute(render(sql, ctx.values))


def check_result_match(c: Check, ctx: CheckContext) -> CheckResult:
    sql, why = _answer_select(ctx)
    if sql is None:
        return CheckResult(c.label, False, why)
    try:
        actual = ctx.backend.execute(sql)
    except SqlError as exc:
        return CheckResult(c.label, False, f"answer.sql failed: {exc}")
    expected = _run(ctx, c.params["reference_sql"])
    ok, reason = compare_results(
        actual.rows, expected.rows,
        ordered=bool(c.params.get("ordered", False)),
        abs_tol=float(c.params.get("abs_tol", 0.01)),
        rel_tol=float(c.params.get("rel_tol", 0.0)),
    )
    return CheckResult(c.label, ok, reason)


def check_sql_match(c: Check, ctx: CheckContext) -> CheckResult:
    try:
        actual = _run(ctx, c.params["sql"])
    except SqlError as exc:
        return CheckResult(c.label, False, f"query failed: {exc}")
    expected = _run(ctx, c.params["reference_sql"])
    ok, reason = compare_results(
        actual.rows, expected.rows,
        ordered=bool(c.params.get("ordered", False)),
        abs_tol=float(c.params.get("abs_tol", 0.01)),
        rel_tol=float(c.params.get("rel_tol", 0.0)),
    )
    return CheckResult(c.label, ok, reason)


def _scalar(ctx: CheckContext, sql: str) -> Any:
    res = _run(ctx, sql)
    return res.rows[0][0] if res.rows else None


def check_scalar_equals(c: Check, ctx: CheckContext) -> CheckResult:
    try:
        got = _scalar(ctx, c.params["sql"])
    except SqlError as exc:
        return CheckResult(c.label, False, f"query failed: {exc}")
    if "expected_sql" in c.params:
        want = _scalar(ctx, c.params["expected_sql"])
    else:
        want = c.params["expected"]
    ok = values_equal(normalize_value(got), normalize_value(want))
    return CheckResult(c.label, ok, f"got {got!r}, expected {want!r}")


def check_answer_executes(c: Check, ctx: CheckContext) -> CheckResult:
    sql, why = _answer_select(ctx)
    if sql is None:
        return CheckResult(c.label, False, why)
    try:
        res = ctx.backend.execute(sql)
    except SqlError as exc:
        return CheckResult(c.label, False, f"answer.sql failed: {exc}")
    ncols = len(res.columns) if res.columns else (len(res.rows[0]) if res.rows else 0)
    p = c.params
    if ncols < int(p.get("min_columns", 0)):
        return CheckResult(c.label, False, f"{ncols} columns, expected at least {p['min_columns']}")
    if "max_rows" in p and len(res.rows) > int(p["max_rows"]):
        return CheckResult(c.label, False, f"{len(res.rows)} rows, expected at most {p['max_rows']}")
    if "expected_rows_sql" in p:
        want = _scalar(ctx, p["expected_rows_sql"])
        if int(float(want)) != len(res.rows):
            return CheckResult(c.label, False, f"{len(res.rows)} rows, expected {want}")
    if "max_text_length" in p:
        idx = int(p["max_text_length"]["column_index"])
        limit = int(p["max_text_length"]["max"])
        too_long = [r for r in res.rows if r[idx] is not None and len(str(r[idx])) > limit]
        if too_long:
            return CheckResult(c.label, False, f"column {idx} has values longer than {limit} characters")
    return CheckResult(c.label, True, f"ran OK: {ncols} columns, {len(res.rows)} rows")


def check_answer_contains(c: Check, ctx: CheckContext) -> CheckResult:
    text = ctx.answer_sql() or ""
    flags = re.I if c.params.get("ignore_case", True) else 0
    ok = re.search(c.params["pattern"], text, flags) is not None
    return CheckResult(c.label, ok, "pattern found" if ok else f"pattern {c.params['pattern']!r} not found in answer.sql")


def check_file_equals_sql(c: Check, ctx: CheckContext) -> CheckResult:
    path = ctx.workdir / c.params["file"]
    if not path.exists():
        return CheckResult(c.label, False, f"{c.params['file']} was not written")
    got = path.read_text().strip()
    try:
        want = _scalar(ctx, c.params["sql"])
    except SqlError as exc:
        return CheckResult(c.label, False, f"query failed: {exc}")
    ok = values_equal(normalize_value(got), normalize_value(want), abs_tol=0.0)
    return CheckResult(c.label, ok, f"file has {got!r}, warehouse has {want!r}")


def check_s3_objects(c: Check, ctx: CheckContext) -> CheckResult:
    if ctx.s3_lister is None:
        return CheckResult(c.label, False, "no S3 access configured")
    prefix = render(c.params["prefix"], ctx.values)
    keys = ctx.s3_lister(prefix)
    suffix = c.params.get("suffix")
    if suffix:
        keys = [k for k in keys if k.lower().endswith(suffix.lower())]
    need = int(c.params.get("min_count", 1))
    ok = len(keys) >= need
    return CheckResult(c.label, ok, f"{len(keys)} matching objects under {prefix}")


HANDLERS = {
    "result_match": check_result_match,
    "sql_match": check_sql_match,
    "scalar_equals": check_scalar_equals,
    "answer_executes": check_answer_executes,
    "answer_contains": check_answer_contains,
    "file_equals_sql": check_file_equals_sql,
    "s3_objects": check_s3_objects,
}


def run_checks(task: Task, ctx: CheckContext) -> list[CheckResult]:
    results = []
    for c in task.checks:
        try:
            results.append(HANDLERS[c.type](c, ctx))
        except Exception as exc:  # a broken check must never crash a whole run
            results.append(CheckResult(c.label, False, f"check error: {exc}"))
    return results
