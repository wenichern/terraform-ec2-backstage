# aws-ops-mcp

A standalone MCP server that exposes EC2 AMI-drift status as read-only chat tools.
It reuses `ami_checker.py` — the same drift-detection logic that drives the
CloudWatch alarm / Lambda pipeline in `ami-poc-demo/` — so the chat's answer,
the metric, and the alarm can never disagree about what "outdated" means.

Part of the Backstage IDP "intelligence hub" PoC:
`Detect (CloudWatch alarm) -> Investigate (mcp-chat, this tool) -> Decide (human)
-> Fix (Backstage golden path -> CI/CD -> Terraform) -> Record (ServiceNow)`.
This piece covers **Investigate**: "which EC2 need an AMI update" answered live,
in chat, without anyone grepping the AWS console.

## Tools exposed

| Tool | Arguments | What it does |
|---|---|---|
| `list_outdated_ec2` | `service?` (backstage-entity tag), `region?` | Lists running EC2 instances whose AMI is drifted, deprecated, or missing |
| `check_ec2_ami` | `instance_id`, `region?` | Status of one specific instance |

Both are **read-only** — they never change anything in AWS. They call
`ami_checker.scan()` live against EC2/SSM each time they're invoked; nothing
is cached.

## Requirements

- Python 3.12+
- AWS credentials with at least: `ec2:DescribeInstances`, `ec2:DescribeImages`,
  `ssm:GetParameter` (same minimal permissions as the checker Lambda in
  `ami-poc-demo/terraform/detect-platform/main.tf`)

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_REGION=us-east-1        # or wherever your instances run
python server.py                   # listens on http://127.0.0.1:9002/mcp
```

Leave it running in its own terminal — same pattern as the `rag-bridge` server.

### Config (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Region to scan |
| `LATEST_AMI_SSM_PARAM` | `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64` | What "latest" is compared against |
| `BRIDGE_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only behind a network boundary — this server has no auth of its own |
| `BRIDGE_PORT` | `9002` | |

Copy `.env.example` to `.env` and `set -a && source .env && set +a` before
running, or export the variables directly.

## Test it

```bash
pip install -r requirements-dev.txt
pytest                              # 3 tests, mocked AWS via moto, no real credentials needed
```

```bash
python smoke_test.py                # hits the REAL running server + REAL AWS; needs credentials
```

## Wire it into Backstage / mcp-chat

Add an entry under `mcpServers` in `app-config.yaml`, alongside any other MCP
servers (e.g. `rag-bridge`):

```yaml
mcpChat:
  mcpServers:
    - name: aws-ops
      type: streamable-http
      url: http://localhost:9002/mcp
```

Restart the Backstage backend, then ask mcp-chat something like:
> Which EC2 need an AMI update?

It should call `list_outdated_ec2` and relay the answer back with instance
IDs, current vs. latest AMI, and the reason.

## Run as a container

```bash
docker build -t aws-ops-mcp .
docker run -d -p 9002:9002 --env-file .env aws-ops-mcp
```

If the container needs AWS credentials, mount them or pass them as env vars
(e.g. `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) —
don't bake long-lived credentials into the image.

## Known limitation: duplicated checker code

`ami_checker.py` in this folder is currently a **copy** of
`ami-poc-demo/checker/ami_checker.py`, not a shared import. If the drift
logic changes in one place, update the other too, or the chat tool and the
CloudWatch alarm can silently disagree about what's outdated.

To remove the duplication later: delete this folder's copy and have
`server.py` import it via a relative path into `ami-poc-demo/checker/`, or
extract the checker into a small shared/installable package once more than
one consumer needs it.

## Design notes

- No authentication of its own — bind to `127.0.0.1` unless it sits behind a
  network boundary or gateway that handles auth, same caution as `rag-bridge`.
- Every user of the chat shares whatever AWS credentials this process runs
  with. Scope those credentials to read-only, least-privilege.
- Stateless and read-only by design — safe to restart anytime, nothing to
  reconcile or recover.
