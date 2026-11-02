"""Load and validate adab.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class Arm:
    """One way of running the agent, e.g. 'baseline' (no skills) or 'skills'."""

    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 900


@dataclass
class Config:
    region: str
    workgroup: str
    database: str
    admin_secret_arn: str
    output_bucket: str
    arms: dict[str, Arm]
    stack_name: str = "adab"
    harness_profile: str | None = None
    agent_profile: str | None = "adab-agent"
    history_delay_s: int = 15
    results_dir: str = "results"
    path: Path | None = None

    def s3_prefix(self, *parts: str) -> str:
        clean = "/".join(p.strip("/") for p in parts if p)
        return f"s3://{self.output_bucket}/{clean}/"


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}. Run `adab setup` first, or copy adab.example.yaml.")
    data = yaml.safe_load(path.read_text()) or {}

    required = ["region", "workgroup", "database", "admin_secret_arn", "output_bucket", "arms"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ConfigError(f"Missing keys in {path}: {', '.join(missing)}")

    arms: dict[str, Arm] = {}
    for name, spec in (data.get("arms") or {}).items():
        if not isinstance(spec, dict) or not spec.get("command"):
            raise ConfigError(f"Arm '{name}' needs a 'command' list.")
        command = spec["command"]
        if isinstance(command, str):
            raise ConfigError(f"Arm '{name}': 'command' must be a list of arguments, not a string.")
        arms[name] = Arm(
            name=name,
            command=[str(c) for c in command],
            env={k: _expand(str(v)) for k, v in (spec.get("env") or {}).items()},
            timeout_s=int(spec.get("timeout_s", 900)),
        )

    return Config(
        region=data["region"],
        workgroup=data["workgroup"],
        database=data["database"],
        admin_secret_arn=data["admin_secret_arn"],
        output_bucket=data["output_bucket"],
        arms=arms,
        stack_name=data.get("stack_name", "adab"),
        harness_profile=data.get("harness_profile"),
        agent_profile=data.get("agent_profile", "adab-agent"),
        history_delay_s=int(data.get("history_delay_s", 15)),
        results_dir=data.get("results_dir", "results"),
        path=path,
    )
