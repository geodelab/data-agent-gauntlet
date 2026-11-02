from pathlib import Path

import pytest

from adab.tasks import CATEGORIES, load_tasks, load_traps, render
from adab.runner import scan_traps

TASKS = Path(__file__).resolve().parents[1] / "tasks"


def test_all_tasks_load_and_cover_every_category():
    tasks = load_tasks(TASKS)
    assert len(tasks) == 20
    assert {t.category for t in tasks} == CATEGORIES
    for t in tasks:
        assert t.checks, t.id
        assert "answer.sql" not in t.prompt or t.category == "query"


def test_task_filter_globs():
    tasks = load_tasks(TASKS, ["query-*"])
    assert tasks and all(t.id.startswith("query-") for t in tasks)


def test_render_only_replaces_known_placeholders():
    assert render("{s3_output}x {other}", {"s3_output": "s3://b/p/"}) == "s3://b/p/x {other}"


@pytest.mark.parametrize("sql,expected", [
    ("SELECT string_agg(name, ',') FROM t", {"string_agg"}),
    ("CREATE INDEX i ON t(c)", {"create_index"}),
    ("CREATE TABLE t (id SERIAL, note text, b int)", {"serial", "text_column"}),
    ("SELECT * FROM stl_query", {"stl_stv_views"}),
    ("SELECT SUBSTR(query_text, 1, 20) FROM sys_query_history", {"substr"}),
    ("INSERT INTO t VALUES (1) RETURNING id", {"returning"}),
    ("INSERT INTO t VALUES (1) ON CONFLICT (id) DO NOTHING", {"on_conflict"}),
    ("SELECT DISTINCT ON (a) a FROM t", {"distinct_on"}),
    ("SELECT LISTAGG(v, ', ') WITHIN GROUP (ORDER BY v), query_text FROM sys_query_history", set()),
    ("SELECT SUBSTRING(query_text, 1, 20) FROM sys_query_history", set()),
])
def test_trap_patterns(sql, expected):
    traps = load_traps(TASKS)
    assert set(scan_traps([sql], traps)) == expected
