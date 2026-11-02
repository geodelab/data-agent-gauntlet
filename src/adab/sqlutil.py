"""Small SQL helpers: statement splitting and result normalization."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

_NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]00:00:00(\.0+)?$")


def split_statements(sql: str) -> list[str]:
    """Split a SQL script on semicolons, ignoring semicolons inside quotes and comments."""
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    in_single = in_double = in_line_comment = in_block_comment = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 1
                in_block_comment = False
        elif in_single:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 1
            elif ch == "'":
                in_single = False
        elif in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch)
        elif ch == "'":
            in_single = True
            buf.append(ch)
        elif ch == '"':
            in_double = True
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if strip_comments(stmt).strip():
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if strip_comments(tail).strip():
        statements.append(tail)
    return statements


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def first_keyword(sql: str) -> str:
    words = strip_comments(sql).strip().split()
    return words[0].upper() if words else ""


def is_read_only_query(sql: str) -> bool:
    """True if the text is exactly one SELECT/WITH statement."""
    stmts = split_statements(sql)
    if len(stmts) != 1:
        return False
    if first_keyword(stmts[0]) not in {"SELECT", "WITH"}:
        return False
    body = strip_comments(stmts[0]).upper()
    blocked = r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|UNLOAD|COPY|CALL)\b"
    return re.search(blocked, body) is None


def normalize_value(v: Any) -> Any:
    """Make values from different SQL spellings comparable."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).rstrip()  # CHAR columns are blank padded
    if _NUMERIC.match(s):
        return float(s)
    m = _MIDNIGHT.match(s)
    if m:
        return m.group(1)
    return s


def normalize_rows(rows: Iterable[Iterable[Any]]) -> list[tuple]:
    return [tuple(normalize_value(v) for v in row) for row in rows]


def _sort_key(row: tuple) -> tuple:
    key = []
    for v in row:
        if v is None:
            key.append((0, ""))
        elif isinstance(v, float):
            key.append((1, f"{round(v, 2):020.2f}"))
        else:
            key.append((2, str(v)))
    return tuple(key)


def values_equal(a: Any, b: Any, abs_tol: float = 0.01, rel_tol: float = 0.0) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isclose(a, b, abs_tol=abs_tol):
            return True
        return rel_tol > 0 and math.isclose(a, b, rel_tol=rel_tol)
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower()[:1] == str(b).lower()[:1]
    return a == b


def compare_results(
    actual: list[tuple],
    expected: list[tuple],
    ordered: bool = False,
    abs_tol: float = 0.01,
    rel_tol: float = 0.0,
) -> tuple[bool, str]:
    """Compare two result sets. Returns (passed, reason)."""
    a, e = normalize_rows(actual), normalize_rows(expected)
    if len(a) != len(e):
        return False, f"row count {len(a)} != expected {len(e)}"
    if a and len(a[0]) != len(e[0]):
        return False, f"column count {len(a[0])} != expected {len(e[0])}"
    if not ordered:
        a, e = sorted(a, key=_sort_key), sorted(e, key=_sort_key)
    for i, (ra, re_) in enumerate(zip(a, e)):
        for j, (va, ve) in enumerate(zip(ra, re_)):
            if not values_equal(va, ve, abs_tol=abs_tol, rel_tol=rel_tol):
                return False, f"row {i}, column {j}: got {va!r}, expected {ve!r}"
    return True, "result matches reference"
