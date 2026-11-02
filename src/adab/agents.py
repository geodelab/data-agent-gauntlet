"""Run a coding agent (Claude Code, Kiro, Codex, ...) as a subprocess on one task."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Arm


@dataclass
class AgentRun:
    exit_code: int | None
    duration_s: float
    timed_out: bool
    stdout: str
    stderr: str
    meta: dict[str, Any] = field(default_factory=dict)


def _parse_meta(stdout: str) -> dict[str, Any]:
    """Pull usage fields out of JSON output when the agent provides it
    (e.g. `claude -p --output-format json`). Unknown formats are ignored."""
    text = stdout.strip()
    if not text:
        return {}
    candidates = [text] + [line for line in reversed(text.splitlines()) if line.strip().startswith("{")]
    for cand in candidates:
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, dict):
            keep = ("num_turns", "total_cost_usd", "duration_ms", "is_error", "session_id")
            return {k: data[k] for k in keep if k in data}
    return {}


def build_command(arm: Arm, prompt: str) -> tuple[list[str], str | None]:
    """Substitute {prompt}. If no argument contains it, the prompt goes to stdin."""
    if any("{prompt}" in part for part in arm.command):
        return [part.replace("{prompt}", prompt) for part in arm.command], None
    return list(arm.command), prompt


def run_agent(arm: Arm, prompt: str, workdir: Path, extra_env: dict[str, str] | None = None) -> AgentRun:
    cmd, stdin_text = build_command(arm, prompt)
    env = dict(os.environ)
    env.update(extra_env or {})
    env.update(arm.env)
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_text,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=arm.timeout_s,
        )
        return AgentRun(
            exit_code=proc.returncode,
            duration_s=round(time.time() - start, 1),
            timed_out=False,
            stdout=proc.stdout,
            stderr=proc.stderr,
            meta=_parse_meta(proc.stdout),
        )
    except subprocess.TimeoutExpired as exc:
        return AgentRun(
            exit_code=None,
            duration_s=round(time.time() - start, 1),
            timed_out=True,
            stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=(exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
        )
    except FileNotFoundError:
        return AgentRun(exit_code=127, duration_s=0.0, timed_out=False, stdout="",
                        stderr=f"Agent command not found: {cmd[0]}")
