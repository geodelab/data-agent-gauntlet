# Running the benchmark

This guide takes you from an empty AWS account to a published scorecard. Plan on about one hour of setup and one to three hours of mostly unattended runs.

## 1. Prerequisites

- **Python and tools:** Python 3.10+, and AWS CLI v2 with an admin-capable profile for setup (called `admin` below).
- **Account:** a sandbox AWS account is strongly recommended.
- **Agent CLI:** Claude Code, or Kiro CLI, logged in and working on your machine.

```bash
pip install -e ".[dev]"
dagt validate
```

## 2. Create the sandbox

```bash
dagt setup --region us-east-1 --profile admin --daily-rpu-hours 20
```

This deploys `infra/adab-stack.yaml`, which takes about 5–10 minutes:

- **Redshift Serverless:** a namespace `adab-ns` and workgroup `adab-wg` at 8 RPU base capacity.
- **Output bucket:** a private S3 bucket for `UNLOAD` output, with objects expiring after 7 days.
- **Redshift role:** the namespace default role, used to read the public sample bucket and write the output bucket.
- **Agent role:** `AgentRole`, the only identity the AI agent uses.

It then loads TICKIT into schema `bench`, sets the daily compute limit, and writes `adab.yaml`. Add the printed profile to `~/.aws/config`:

```ini
[profile adab-agent]
role_arn = arn:aws:iam::123456789012:role/adab-AgentRole-XXXX
source_profile = admin
region = us-east-1
```

Then run:

```bash
dagt doctor
```

## 3. Configure the arms

`adab.yaml` contains two arms that run the same agent command. What changes between them is whether the agent has the AWS skills installed when the arm runs.

```yaml
arms:
  baseline:
    command: ["claude", "-p", "{prompt}", "--output-format", "json", "--dangerously-skip-permissions"]
    timeout_s: 900
  skills:
    command: ["claude", "-p", "{prompt}", "--output-format", "json", "--dangerously-skip-permissions"]
    timeout_s: 900
```

`{prompt}` is replaced with the task prompt. If no argument contains `{prompt}`, the prompt is sent on stdin. The harness sets `AWS_PROFILE=adab-agent` and `AWS_REGION` for the agent process.

**Kiro CLI.** Headless mode needs an API key (`KIRO_API_KEY`). Check `kiro-cli chat --help` for your version:

```yaml
  kiro-skills:
    command: ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "{prompt}"]
    env:
      KIRO_API_KEY: "${KIRO_API_KEY}"
    timeout_s: 900
```

> Flags change between agent versions. Run one task first (`--task query-listagg-venues`) and open the attempt folder to confirm the agent actually worked.

## 4. Run

**Baseline: the plugin must not be installed.**

```bash
dagt run --arm baseline --run-id main --repeat 3
```

**Skills: install the plugin, then run the same tasks.**

```bash
# Claude Code
claude
/plugin install aws-data-analytics@claude-plugins-official
/reload-plugins
# exit, then:
dagt run --arm skills --run-id main --repeat 3
```

For Kiro, follow the Kiro section of the Agent Toolkit README (`npx skills add aws/agent-toolkit-for-aws/skills` plus the AWS MCP Server entry).

Each attempt writes a folder:

```
results/main/<arm>/<task>/attempt-N/
  PROMPT.md            exact prompt sent
  answer.sql           what the agent saved
  agent_stdout.txt     agent output (JSON for Claude Code)
  agent_queries.json   every statement the agent sent to Redshift, with status and error
```

## 5. Report

```bash
dagt report results/main
```

This writes `results/main/summary.md` (headline table, categories, PostgreSQL habits, per task) and `attempts.csv`.

## 6. Tear down

```bash
dagt teardown --profile admin
```

## Good practice for publishing numbers

- **Repeats:** run at least 3 repeats per arm, and report pass rate and first-try rate with the spread.
- **Disclose the setup:** agent name and version, model, plugin version, region and date.
- **Show the evidence:** publish `summary.md` and the attempt folders (they contain no credentials) so others can check your claims.
- **Fair comparison:** run the arms back to back so both see the same service versions.
