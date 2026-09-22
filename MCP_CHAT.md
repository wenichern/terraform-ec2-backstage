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

`list_outdated_ec2` leads its response with one of two explicit status
words so the LLM can't paraphrase past the distinction:
- `UP TO DATE - ...` — a real, verified result: instances were found and
  none are outdated.
- `UNKNOWN - ...` — a `service` filter matched zero running, tagged
  instances. This is NOT the same as healthy; see the bug history below.

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

Add an entry under `mcpServers` in `app-config.local.yaml` (this plugin's
actual config lives there, under `mcpChat.mcpServers` — alongside any other
entries like `backstage-server`). No `type` field; `headers` is optional and
was not needed for this local, unauthenticated server:

```yaml
mcpChat:
  mcpServers:
    - id: backstage-server
      name: Backstage Server
      url: 'http://localhost:7007/api/mcp-actions/v1/aws'
      headers:
        Authorization: 'Bearer ${MCP_TOKEN}'
      disabledTools:
        - scaffolder.list-scaffolder-actions

    - id: aws-ops
      name: AWS Ops
      url: 'http://localhost:9002/mcp'
```

Restart the Backstage backend (`yarn start`), then confirm the connection in
the startup log:
```
mcp-chat info MCP Server 'AWS Ops' connected via streamable-http with tools: list_outdated_ec2, check_ec2_ami
mcp-chat info All MCP servers connected successfully. Total tools: 10
```

## Tested scenarios (verified live, through the actual chat UI)

These four questions were asked in mcp-chat, backed by this server, against
a real deployed instance (`i-021997d25f431b45b`, tagged
`component:default/ami-poc-demo`, intentionally on an older AMI):

| # | Question asked in chat | Tool called | Result |
|---|---|---|---|
| 1 | "Which EC2 need an AMI update?" | `list_outdated_ec2` (no filter) | Correctly listed the one drifted instance with current/latest AMI and reason |
| 2 | "Is `component:default/ami-poc-demo` up to date?" | `list_outdated_ec2(service=...)` | Correctly said no, with the same drift details |
| 3 | "Is `payments-api` up to date?" (service that doesn't exist) | `list_outdated_ec2(service="payments-api")` | After the fix: correctly reported it cannot determine status — no instances found — rather than falsely saying "up to date" (see Bug history below) |
| 4 | "Check instance `i-021997d25f431b45b`" | `check_ec2_ami` | Correctly returned that specific instance's drift status |

### Testing a tool directly, bypassing the chat/LLM

Useful for isolating "is the server wrong" from "is the LLM paraphrasing the
server's answer wrong" — call a tool directly with a real MCP client instead
of going through mcp-chat:

```bash
source .venv/bin/activate
python -c "
import asyncio
from fastmcp import Client

async def main():
    async with Client('http://127.0.0.1:9002/mcp') as c:
        r = await c.call_tool('list_outdated_ec2', {'service': 'payments-api'})
        print(r.content[0].text)

asyncio.run(main())
"
```

This is exactly how the bug in scenario 3 was root-caused: the direct call
showed the server's raw output was already correct, which proved the
remaining problem was in how the LLM summarized it, not in the server —
see "Bug history" below.

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

## Gotcha: restarting this server invalidates Backstage's MCP session

mcp-chat opens a session with this server when Backstage starts, and reuses
that session ID on every tool call. If you restart `python server.py` (kill
and re-run the process - not just save an edit while it's already running),
the new process has no memory of the old session ID, and every tool call
fails with:

```
Error executing tool 'list_outdated_ec2': Streamable HTTP error: Error
POSTing to endpoint: {"jsonrpc":"2.0","id":null,"error":{"code":-32600,
"message":"Session not found"}}
```

or, before mcp-chat even makes a real call, a `400 Bad Request: Missing
session ID` if something hits the endpoint without going through the
handshake at all.

**Fix**: after restarting this server's process, also restart Backstage's
backend (`yarn start`) so mcp-chat reconnects and gets a fresh session. Look
for this line in the startup log to confirm it worked:
```
mcp-chat info MCP Server 'AWS Ops' connected via streamable-http with tools: list_outdated_ec2, check_ec2_ami
```

You do **not** need to restart Backstage if you only edited this server's
*code* without restarting the *process* - the already-running process just
keeps serving the old code until you do restart it. The two-restart
requirement only applies when the server process itself bounces.

## Bug history: "zero results" silently read as "healthy"

Worth knowing if you extend this tool - **filtering by a service that
matches zero running instances must never be phrased as "up to date."**
This was caught via live testing, not code review, and took two attempts to
fully fix:

1. **First bug**: `list_outdated_ec2(service="payments-api")` on a service
   with no matching instances returned *"All 0 scanned instance(s) for
   payments-api are on the latest AMI"* - technically following from the
   code, but indistinguishable from a real, verified healthy result. The
   chat relayed this as a confident "yes, fully up to date."
2. **First fix attempt (insufficient)**: changed the message to explain in
   prose that zero results means unknown, not healthy, ending with "...not
   that it's up to date." The raw tool output was correct, but the LLM
   (Gemini) paraphrased the warning away and still told the user "up to
   date" - the caveat was there but easy to summarize past.
3. **Working fix**: responses now lead with an unambiguous status word -
   `UNKNOWN - ...` when no instances match, `UP TO DATE - ...` only for a
   real verified-healthy result - and the tool's docstring explicitly
   instructs the model: "Do NOT report this as up to date or healthy."
   Verified against the live chat UI, not just unit tests.

**Takeaway for future tools here**: an LLM will paraphrase prose warnings
into whatever sounds most reassuring. Lead tool responses with an
unambiguous status token when the distinction matters, and repeat the
instruction in the tool's docstring - test any "can't determine" or
"not found" path through the actual chat UI, not just the raw tool
response, since the LLM's summarization is itself part of what can fail.


## Design notes

- No authentication of its own — bind to `127.0.0.1` unless it sits behind a
  network boundary or gateway that handles auth, same caution as `rag-bridge`.
- Every user of the chat shares whatever AWS credentials this process runs
  with. Scope those credentials to read-only, least-privilege.
- Stateless and read-only by design — safe to restart anytime, nothing to
  reconcile or recover.
