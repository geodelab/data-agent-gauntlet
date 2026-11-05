"""Command line interface: adab setup | doctor | run | report | teardown | validate."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__

DEFAULT_CONFIG = "adab.yaml"
DEFAULT_TASKS = "tasks"


def _session(profile: str | None, region: str):
    import boto3

    return boto3.Session(profile_name=profile, region_name=region)


def _backend(cfg):
    from .backend import RedshiftDataBackend

    return RedshiftDataBackend(_session(cfg.harness_profile, cfg.region), cfg.workgroup, cfg.database,
                               cfg.admin_secret_arn)


def cmd_validate(args) -> int:
    from .tasks import load_tasks, load_traps

    tasks = load_tasks(args.tasks)
    traps = load_traps(args.tasks)
    by_cat: dict[str, int] = {}
    for t in tasks:
        by_cat[t.category] = by_cat.get(t.category, 0) + 1
    print(f"OK: {len(tasks)} tasks, {len(traps)} trap patterns")
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat}: {n}")
    return 0


def cmd_setup(args) -> int:
    from . import infra
    from .backend import RedshiftDataBackend

    session = _session(args.profile, args.region)
    outputs = infra.deploy_stack(session, args.stack_name, args.prefix, args.base_capacity, args.trusted_principal,
                                 args.subnet_ids)
    infra.wait_workgroup_available(session, outputs["WorkgroupName"])
    secret = infra.admin_secret_arn(session, outputs["NamespaceName"])
    infra.set_usage_limit(session, outputs["WorkgroupArn"], args.daily_rpu_hours)
    backend = RedshiftDataBackend(session, outputs["WorkgroupName"], "dev", secret)
    counts = infra.load_tickit(backend)
    print("Loaded rows: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    infra.write_config(Path(args.config), args.region, args.stack_name, outputs, secret, args.profile, args.agent_profile)
    print(f"\nWrote {args.config}.")
    print("\nNext: add this profile to ~/.aws/config so the agent runs with the limited role:\n")
    print(f"[profile {args.agent_profile}]")
    print(f"role_arn = {outputs['AgentRoleArn']}")
    print(f"source_profile = {args.profile or 'default'}")
    print(f"region = {args.region}\n")
    print("Then run: adab doctor")
    return 0


def cmd_doctor(args) -> int:
    from .config import load_config

    cfg = load_config(args.config)
    ok = True

    def report(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"{'✓' if passed else '✗'} {label}{(' — ' + detail) if detail else ''}")

    for profile, label in ((cfg.harness_profile, "harness"), (cfg.agent_profile, "agent")):
        try:
            ident = _session(profile, cfg.region).client("sts").get_caller_identity()
            report(f"{label} credentials ({profile or 'default'})", True, ident["Arn"])
        except Exception as exc:
            report(f"{label} credentials ({profile or 'default'})", False, str(exc))

    try:
        backend = _backend(cfg)
        n = backend.scalar("SELECT COUNT(*) FROM bench.sales")
        report("Redshift reachable and TICKIT loaded", int(n) > 0, f"bench.sales has {n} rows")
    except Exception as exc:
        report("Redshift reachable and TICKIT loaded", False, str(exc))

    for name, arm in cfg.arms.items():
        exe = arm.command[0]
        report(f"arm '{name}' command found", shutil.which(exe) is not None, exe)
    print("\nReady to run." if ok else "\nFix the items marked ✗, then run doctor again.")
    return 0 if ok else 1


def cmd_run(args) -> int:
    from .config import load_config
    from .infra import s3_lister
    from .report import write_report
    from .runner import run_benchmark
    from .tasks import load_tasks, load_traps

    cfg = load_config(args.config)
    arms = args.arm or list(cfg.arms)
    unknown = [a for a in arms if a not in cfg.arms]
    if unknown:
        print(f"Unknown arm(s): {', '.join(unknown)}. Configured: {', '.join(cfg.arms)}", file=sys.stderr)
        return 2
    tasks = load_tasks(args.tasks, args.task)
    if not tasks:
        print("No tasks matched.", file=sys.stderr)
        return 2
    traps = load_traps(args.tasks)
    session = _session(cfg.harness_profile, cfg.region)
    run_dir = run_benchmark(cfg, tasks, traps, _backend(cfg), arms, repeat=args.repeat,
                            s3_lister=s3_lister(session), run_id=args.run_id)
    out = write_report(run_dir)
    print(f"\nDone. Scorecard: {out}")
    return 0


def cmd_report(args) -> int:
    from .report import write_report

    out = write_report(Path(args.run_dir))
    print(out.read_text())
    return 0


def cmd_teardown(args) -> int:
    from . import infra

    if not args.yes:
        answer = input(f"Delete stack '{args.stack_name}' and everything in it? Type the stack name to confirm: ")
        if answer.strip() != args.stack_name:
            print("Cancelled.")
            return 1
    infra.delete_stack(_session(args.profile, args.region), args.stack_name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="adab", description="Benchmark AI coding agents on real AWS data tasks.")
    p.add_argument("--version", action="version", version=f"adab {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="Check task files (no AWS access needed).")
    v.add_argument("--tasks", default=DEFAULT_TASKS)
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("setup", help="Create the sandbox stack, load TICKIT, write adab.yaml.")
    s.add_argument("--region", default="us-east-1")
    s.add_argument("--profile", default=None, help="AWS profile with admin rights to create the stack.")
    s.add_argument("--stack-name", default="adab")
    s.add_argument("--prefix", default="adab")
    s.add_argument("--base-capacity", type=int, default=8)
    s.add_argument("--daily-rpu-hours", type=int, default=20, help="Hard daily compute cap for the workgroup.")
    s.add_argument("--trusted-principal", default="", help="Optional ARN allowed to assume the agent role.")
    s.add_argument("--subnet-ids", default="",
                   help="Comma-separated subnets in 3 AZs, for accounts without a default VPC.")
    s.add_argument("--agent-profile", default="adab-agent")
    s.add_argument("--config", default=DEFAULT_CONFIG)
    s.set_defaults(func=cmd_setup)

    d = sub.add_parser("doctor", help="Check credentials, Redshift and agent commands.")
    d.add_argument("--config", default=DEFAULT_CONFIG)
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("run", help="Run the benchmark.")
    r.add_argument("--config", default=DEFAULT_CONFIG)
    r.add_argument("--tasks", default=DEFAULT_TASKS)
    r.add_argument("--arm", action="append", help="Arm to run (repeatable). Default: all arms.")
    r.add_argument("--task", action="append", help="Task id or glob, e.g. 'query-*' (repeatable).")
    r.add_argument("--repeat", type=int, default=1, help="Attempts per task per arm.")
    r.add_argument("--run-id", default=None,
                   help="Reuse a run id to add another arm to the same results (e.g. 'main').")
    r.set_defaults(func=cmd_run)

    rp = sub.add_parser("report", help="Rebuild the scorecard for a finished run.")
    rp.add_argument("run_dir")
    rp.set_defaults(func=cmd_report)

    t = sub.add_parser("teardown", help="Delete the sandbox stack and its data.")
    t.add_argument("--region", default="us-east-1")
    t.add_argument("--profile", default=None)
    t.add_argument("--stack-name", default="adab")
    t.add_argument("--yes", action="store_true")
    t.set_defaults(func=cmd_teardown)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
