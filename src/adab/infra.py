"""Create and delete the benchmark sandbox (CloudFormation + data load + usage limit)."""

from __future__ import annotations

import time
from importlib import resources
from pathlib import Path

import yaml

from .backend import RedshiftDataBackend

TEMPLATE = Path(__file__).resolve().parents[2] / "infra" / "adab-stack.yaml"

# Both arms run the same command. The difference is whether the aws-data-analytics
# plugin is installed when the arm runs (see docs/running-the-benchmark.md).
DEFAULT_ARMS = {
    "baseline": {
        "command": ["claude", "-p", "{prompt}", "--output-format", "json", "--dangerously-skip-permissions"],
        "timeout_s": 900,
    },
    "skills": {
        "command": ["claude", "-p", "{prompt}", "--output-format", "json", "--dangerously-skip-permissions"],
        "timeout_s": 900,
    },
}


def _outputs(cfn, stack_name: str) -> dict[str, str]:
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def deploy_stack(session, stack_name: str, prefix: str, base_capacity: int, trusted_principal: str,
                 subnet_ids: str = "", log=print) -> dict[str, str]:
    cfn = session.client("cloudformation")
    params = [
        {"ParameterKey": "Prefix", "ParameterValue": prefix},
        {"ParameterKey": "BaseCapacity", "ParameterValue": str(base_capacity)},
        {"ParameterKey": "AgentTrustedPrincipalArn", "ParameterValue": trusted_principal or ""},
        {"ParameterKey": "SubnetIds", "ParameterValue": subnet_ids or ""},
    ]
    body = TEMPLATE.read_text()
    try:
        cfn.describe_stacks(StackName=stack_name)
        exists = True
    except cfn.exceptions.ClientError:
        exists = False

    if exists:
        log(f"Stack {stack_name} exists, updating…")
        try:
            cfn.update_stack(StackName=stack_name, TemplateBody=body, Parameters=params,
                             Capabilities=["CAPABILITY_IAM"])
            cfn.get_waiter("stack_update_complete").wait(StackName=stack_name)
        except cfn.exceptions.ClientError as exc:
            if "No updates are to be performed" not in str(exc):
                raise
    else:
        log(f"Creating stack {stack_name} (Redshift Serverless takes about 5–10 minutes)…")
        cfn.create_stack(StackName=stack_name, TemplateBody=body, Parameters=params,
                         Capabilities=["CAPABILITY_IAM"], Tags=[{"Key": "project", "Value": "aws-data-agent-bench"}])
        cfn.get_waiter("stack_create_complete").wait(StackName=stack_name,
                                                     WaiterConfig={"Delay": 20, "MaxAttempts": 90})
    return _outputs(cfn, stack_name)


def admin_secret_arn(session, namespace: str) -> str:
    rs = session.client("redshift-serverless")
    for _ in range(30):
        ns = rs.get_namespace(namespaceName=namespace)["namespace"]
        arn = ns.get("adminPasswordSecretArn")
        if arn:
            return arn
        time.sleep(10)
    raise RuntimeError("The namespace did not report an admin secret ARN.")


def wait_workgroup_available(session, workgroup: str, log=print) -> None:
    rs = session.client("redshift-serverless")
    for _ in range(60):
        status = rs.get_workgroup(workgroupName=workgroup)["workgroup"]["status"]
        if status == "AVAILABLE":
            return
        log(f"Workgroup status: {status}…")
        time.sleep(15)
    raise RuntimeError("Workgroup did not become AVAILABLE in time.")


def load_tickit(backend: RedshiftDataBackend, log=print) -> dict[str, int]:
    sql = resources.files("adab").joinpath("sql/setup_tickit.sql").read_text()
    log("Loading the TICKIT sample dataset into schema `bench`…")
    backend.execute_script(sql)
    counts = {}
    for table in ("users", "venue", "category", "date", "event", "listing", "sales"):
        counts[table] = int(backend.scalar(f"SELECT COUNT(*) FROM bench.{table}"))
    return counts


def set_usage_limit(session, workgroup_arn: str, rpu_hours_per_day: int, log=print) -> None:
    rs = session.client("redshift-serverless")
    existing = rs.list_usage_limits(resourceArn=workgroup_arn).get("usageLimits", [])
    for lim in existing:
        if lim.get("usageType") == "serverless-compute":
            log(f"Usage limit already set: {lim.get('amount')} RPU-hours/{lim.get('period')}")
            return
    rs.create_usage_limit(
        resourceArn=workgroup_arn,
        usageType="serverless-compute",
        amount=rpu_hours_per_day,
        period="daily",
        breachAction="deactivate",
    )
    log(f"Usage limit set: {rpu_hours_per_day} RPU-hours per day, then the workgroup stops accepting queries.")


def write_config(path: Path, region: str, stack_name: str, outputs: dict[str, str], secret_arn: str,
                 harness_profile: str | None, agent_profile: str) -> None:
    existing = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = {
        "region": region,
        "stack_name": stack_name,
        "workgroup": outputs["WorkgroupName"],
        "database": "dev",
        "admin_secret_arn": secret_arn,
        "output_bucket": outputs["OutputBucketName"],
        "agent_role_arn": outputs["AgentRoleArn"],
        "harness_profile": harness_profile,
        "agent_profile": agent_profile,
        "history_delay_s": (existing or {}).get("history_delay_s", 15),
        "results_dir": (existing or {}).get("results_dir", "results"),
        "arms": (existing or {}).get("arms") or DEFAULT_ARMS,
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def empty_bucket(session, bucket: str, log=print) -> None:
    s3 = session.resource("s3")
    b = s3.Bucket(bucket)
    try:
        b.object_versions.delete()
        b.objects.all().delete()
        log(f"Emptied s3://{bucket}")
    except Exception as exc:  # bucket may already be gone
        log(f"Skipping bucket cleanup: {exc}")


def delete_stack(session, stack_name: str, log=print) -> None:
    cfn = session.client("cloudformation")
    try:
        outputs = _outputs(cfn, stack_name)
    except cfn.exceptions.ClientError:
        log(f"Stack {stack_name} not found. Nothing to delete.")
        return
    if outputs.get("OutputBucketName"):
        empty_bucket(session, outputs["OutputBucketName"], log)
    log(f"Deleting stack {stack_name}…")
    cfn.delete_stack(StackName=stack_name)
    cfn.get_waiter("stack_delete_complete").wait(StackName=stack_name,
                                                 WaiterConfig={"Delay": 20, "MaxAttempts": 90})
    log("Deleted. The Redshift namespace, workgroup, bucket and IAM roles are gone.")


def s3_lister(session):
    s3 = session.client("s3")

    def _list(uri: str) -> list[str]:
        assert uri.startswith("s3://")
        bucket, _, prefix = uri[5:].partition("/")
        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys

    return _list
