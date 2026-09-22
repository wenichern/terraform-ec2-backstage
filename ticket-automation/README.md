# ticket-automation

Lambda that turns a CloudWatch alarm (via the existing `ami-drift-alerts` SNS
topic) into a ServiceNow Change Request, and closes it automatically when the
alarm clears. This is the **Decide/Record** half of:
`Detect -> Investigate -> Decide -> Fix -> Record`.

Built and fully tested **before** a ServiceNow PDI was available, using an
abstracted client so the real logic didn't have to wait:

```
CloudWatch alarm --ALARM--> SNS --> Lambda --> ServiceNowClient.open_change_request()
CloudWatch alarm --OK-----> SNS --> Lambda --> ServiceNowClient.close_ticket()
```

## Files
| File | What |
|---|---|
| `servicenow_client.py` | `ServiceNowClient` interface + `MockServiceNowClient` (in-memory) + `RealServiceNowClient` (Table API, untested live) + `get_client()` factory |
| `lambda_handler.py` | Parses the SNS(CloudWatch alarm) message, routes ALARM/OK, calls the client |
| `test_ticket_automation.py` | 8 tests: idempotent open, close-then-reopen, alarm->ticket, alarm->OK closes the *same* ticket, OK-with-nothing-open, ignored states, batched SNS records |
| `terraform/main.tf` | Lambda + IAM role + SNS subscription onto the existing `detect-platform` topic |

## Design: how this stays swappable

`get_client()` is the single switch:
```python
def get_client() -> ServiceNowClient:
    if os.environ.get("SERVICENOW_INSTANCE_URL"):
        return RealServiceNowClient()
    return MockServiceNowClient()
```
No `SERVICENOW_INSTANCE_URL` set -> `MockServiceNowClient` (in-memory, resets on
cold start - fine for proving the flow, not a real record). Set it (plus
`SERVICENOW_USER` / `SERVICENOW_PASSWORD`) once a PDI exists, and
`lambda_handler.py` needs **zero changes**.

## Idempotency

Every ticket is tagged with `correlation_id` = the CloudWatch alarm name
(e.g. `ami-drift-payments-api`, matching the pattern from
`ami-poc-demo/terraform/ec2-demo/main.tf`). Opening looks up an existing open
ticket with that `correlation_id` first - a flapping alarm or a retried
Lambda invocation reuses the same ticket instead of creating duplicates.
Closing looks the ticket up the same way, so ALARM and OK always land on the
same record.

## Run the tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

## Deploy (once detect-platform is applied)

```bash
cd terraform
terraform init -backend-config="bucket=<state-bucket>" \
  -backend-config="key=ticket-automation.tfstate" \
  -backend-config="region=us-east-1"
terraform apply -var "sns_topic_arn=<output from detect-platform>"
```

Leave `servicenow_instance_url` unset for now (defaults to mock/no-op-ish -
tickets are created in Lambda's memory and vanish on the next cold start).
Once the PDI exists:
```bash
terraform apply -var "sns_topic_arn=..." \
  -var "servicenow_instance_url=https://devXXXXX.service-now.com" \
  -var "servicenow_user=..." -var "servicenow_password=..."
```

## What's NOT done / needs your real PDI to verify

- `RealServiceNowClient` is written against the standard `change_request`
  table and typical field names (`correlation_id`, `state`, `close_notes`,
  `close_code`) - **never tested against a live instance**. Field names,
  required fields, and state-value numbers (e.g. `state=3` for Closed) can
  differ by ServiceNow release/config. Verify against your PDI's schema
  before trusting it.
- No approval-gating logic yet (everything opens as a standard Change
  Request with no policy check) - the "auto-approve low-risk, require human
  for prod" policy engine discussed earlier is a separate, not-yet-built
  piece.
- Credentials are passed as Lambda environment variables (Terraform
  `sensitive = true` hides them from plan/apply output, but they're still
  plaintext in the Lambda console/API) - fine for a PoC, move to Secrets
  Manager before anything real.
