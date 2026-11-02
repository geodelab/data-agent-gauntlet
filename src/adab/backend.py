"""Run SQL against Redshift through the Redshift Data API (as the harness admin)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import HARNESS_TAG
from .sqlutil import split_statements


class SqlError(RuntimeError):
    pass


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)


class SqlBackend(Protocol):
    def execute(self, sql: str) -> QueryResult: ...


def _field_value(f: dict[str, Any]) -> Any:
    if f.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if key in f:
            return f[key]
    if "blobValue" in f:
        return f["blobValue"]
    return None


class RedshiftDataBackend:
    """Executes statements with the namespace admin secret, so the harness can see
    every query in sys_query_history and reset the sandbox schema."""

    def __init__(self, session, workgroup: str, database: str, secret_arn: str,
                 poll_s: float = 0.5, timeout_s: int = 600):
        self.client = session.client("redshift-data")
        self.workgroup = workgroup
        self.database = database
        self.secret_arn = secret_arn
        self.poll_s = poll_s
        self.timeout_s = timeout_s

    def execute(self, sql: str) -> QueryResult:
        resp = self.client.execute_statement(
            WorkgroupName=self.workgroup,
            Database=self.database,
            SecretArn=self.secret_arn,
            Sql=HARNESS_TAG + sql,
        )
        sid = resp["Id"]
        deadline = time.time() + self.timeout_s
        while True:
            desc = self.client.describe_statement(Id=sid)
            status = desc["Status"]
            if status == "FINISHED":
                break
            if status in ("FAILED", "ABORTED"):
                raise SqlError(desc.get("Error") or status)
            if time.time() > deadline:
                try:
                    self.client.cancel_statement(Id=sid)
                finally:
                    raise SqlError(f"Statement timed out after {self.timeout_s}s")
            time.sleep(self.poll_s)

        if not desc.get("HasResultSet"):
            return QueryResult()

        columns: list[str] = []
        rows: list[tuple] = []
        token = None
        while True:
            kwargs = {"Id": sid}
            if token:
                kwargs["NextToken"] = token
            page = self.client.get_statement_result(**kwargs)
            if not columns:
                columns = [c.get("label") or c.get("name") for c in page.get("ColumnMetadata", [])]
            for rec in page.get("Records", []):
                rows.append(tuple(_field_value(f) for f in rec))
            token = page.get("NextToken")
            if not token:
                break
        return QueryResult(columns=columns, rows=rows)

    def execute_script(self, sql: str) -> None:
        for stmt in split_statements(sql):
            self.execute(stmt)

    def scalar(self, sql: str) -> Any:
        res = self.execute(sql)
        if not res.rows:
            return None
        return res.rows[0][0]


def execute_script(backend: SqlBackend, sql: str) -> None:
    for stmt in split_statements(sql):
        backend.execute(stmt)
