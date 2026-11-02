"""Load and validate benchmark task definitions (tasks/*.yaml)."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATEGORIES = {"query", "schema", "loading", "troubleshooting", "optimization"}

CHECK_TYPES = {
    # run the agent's answer.sql (one SELECT) and compare with a reference query
    "result_match": {"reference_sql"},
    # run a harness query and compare with another harness query (e.g. view contents)
    "sql_match": {"sql", "reference_sql"},
    # a harness query must return an expected scalar (or the scalar of expected_sql)
    "scalar_equals": {"sql"},
    # answer.sql (one SELECT) must run; optional shape checks
    "answer_executes": set(),
    # answer.sql must match a regex
    "answer_contains": {"pattern"},
    # a file the agent writes must equal the scalar a harness query returns
    "file_equals_sql": {"file", "sql"},
    # objects must exist under an S3 prefix
    "s3_objects": {"prefix"},
}


class TaskError(ValueError):
    pass


@dataclass
class Check:
    type: str
    params: dict[str, Any]

    @property
    def label(self) -> str:
        return self.params.get("name") or self.type


@dataclass
class Task:
    id: str
    title: str
    category: str
    prompt: str
    checks: list[Check]
    setup_sql: str = ""
    path: Path | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Trap:
    name: str
    pattern: re.Pattern
    description: str


def _validate_check(task_id: str, raw: dict) -> Check:
    ctype = raw.get("type")
    if ctype not in CHECK_TYPES:
        raise TaskError(f"{task_id}: unknown check type {ctype!r}")
    missing = CHECK_TYPES[ctype] - set(raw)
    if missing:
        raise TaskError(f"{task_id}: check {ctype} is missing {', '.join(sorted(missing))}")
    if ctype == "scalar_equals" and "expected" not in raw and "expected_sql" not in raw:
        raise TaskError(f"{task_id}: scalar_equals needs 'expected' or 'expected_sql'")
    params = {k: v for k, v in raw.items() if k != "type"}
    if ctype == "answer_contains":
        try:
            re.compile(params["pattern"])
        except re.error as exc:
            raise TaskError(f"{task_id}: bad regex: {exc}") from exc
    return Check(type=ctype, params=params)


def load_task(path: Path) -> Task:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise TaskError(f"{path.name}: not a mapping")
    for key in ("id", "title", "category", "prompt", "checks"):
        if not raw.get(key):
            raise TaskError(f"{path.name}: missing '{key}'")
    if raw["id"] != path.stem:
        raise TaskError(f"{path.name}: id '{raw['id']}' must match the file name")
    if raw["category"] not in CATEGORIES:
        raise TaskError(f"{raw['id']}: category must be one of {sorted(CATEGORIES)}")
    checks = [_validate_check(raw["id"], c) for c in raw["checks"]]
    return Task(
        id=raw["id"],
        title=raw["title"],
        category=raw["category"],
        prompt=raw["prompt"].strip(),
        checks=checks,
        setup_sql=(raw.get("setup_sql") or "").strip(),
        path=path,
        tags=list(raw.get("tags") or []),
    )


def load_tasks(tasks_dir: str | Path, patterns: list[str] | None = None) -> list[Task]:
    tasks_dir = Path(tasks_dir)
    tasks = []
    for path in sorted(tasks_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        task = load_task(path)
        if patterns and not any(fnmatch.fnmatch(task.id, p) for p in patterns):
            continue
        tasks.append(task)
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise TaskError(f"Duplicate task ids: {', '.join(sorted(dupes))}")
    return tasks


def load_traps(tasks_dir: str | Path) -> list[Trap]:
    path = Path(tasks_dir) / "_traps.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or []
    return [
        Trap(name=t["name"], pattern=re.compile(t["pattern"], re.I), description=t.get("description", ""))
        for t in raw
    ]


def render(text: str, values: dict[str, str]) -> str:
    """Replace {placeholders} without touching other braces in SQL."""
    for key, val in values.items():
        text = text.replace("{" + key + "}", str(val))
    return text
