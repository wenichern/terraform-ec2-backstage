# AMI upgrade PoC: Backstage + CloudWatch + mcp-chat + Terraform + ServiceNow

Flow: **Detect** (CloudWatch alarm) -> **Investigate** (mcp-chat) -> **Decide** (human approves)
-> **Fix** (Backstage golden path -> CI/CD -> Terraform) -> **Record** (update and close ServiceNow).

## The contract everything depends on
| Item | Value |
|---|---|
| EC2 tag `backstage-entity` | Catalog entity ref, e.g. `component:default/payments-api` |
| CloudWatch namespace / metric | `SRE/AmiDrift` / `AmiStatus` (dims: `InstanceId`, `Service`) |
| Metric values | 0 ok, 1 newer AMI available, 2 deprecated or missing |
| Alarm | `ami-drift-<name>`, fires at `AmiStatus >= 1`, notifies the SNS topic |
| Fix | Terraform variable `ami_id` in the service's repo |

## Phases
| # | Piece | Status |
|---|---|---|
| 1 | AMI checker + demo EC2 | built, checker tested with moto |
| 2 | Lambda + schedule + SNS + per-instance alarm | built (Terraform parses; not applied here) |
| 3 | `list_outdated_ec2` MCP tool for mcp-chat | next |
| 4 | Backstage scaffolder template "Upgrade EC2 AMI" | |
| 5 | CI/CD: terraform apply, then ServiceNow update + close | |
| 6 | Alarm -> ServiceNow incident (SNS subscriber); Backstage card | |

## Run the detection demo (phase 1 + 2)
```bash
# 1. Shared detection platform (once)
cd terraform/detect-platform
terraform init && terraform apply -var alert_email=you@example.com   # confirm the SNS email
export TOPIC=$(terraform output -raw sns_topic_arn)

# 2. Demo EC2 on a LEGACY AMI, with its alarm
cd ../ec2-demo
cp terraform.tfvars.example terraform.tfvars      # set ami_id to an older AL2023 AMI
terraform init && terraform apply -var alarm_topic_arn=$TOPIC

# 3. Don't wait for the schedule: run the checker now
aws lambda invoke --function-name ami-checker /tmp/out.json && cat /tmp/out.json

# 4. Watch the alarm go OK -> ALARM (checker publishes every 5 min; alarm period 5 min)
aws cloudwatch describe-alarms --alarm-names ami-drift-ami-poc-demo \
  --query 'MetricAlarms[].[AlarmName,StateValue,StateReason]' --output table
```
Expected: `/tmp/out.json` lists the demo instance with status `drift`; the alarm reaches
`ALARM` within about 5 to 10 minutes and the SNS email arrives.

To test the fix by hand: set `ami_id` to the current AL2023 AMI
(`aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value --output text`),
`terraform apply` (the instance is replaced), invoke the checker again, and the alarm returns to `OK`.

## Local checker and tests (no AWS needed for tests)
```bash
cd checker && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pytest
python ami_checker.py --region us-east-1          # against a real account
```

## Known limits
- Single region per checker deployment. Reads AMI metadata only, so OS-level end of life
  (a distro release, for example) is not visible; add a name-prefix to EOL-date map later.
- "Drift" flags any instance not on the newest AMI, which can be noisy. Raise the alarm
  threshold to 2 to alert only on deprecated/missing AMIs.
- Changing an `aws_instance` AMI replaces it (brief downtime). For real workloads use a
  launch template + Auto Scaling group with instance refresh.
- Terraform was syntax-parsed but not applied in the build sandbox. Run `terraform validate` and review `plan` first.

## Using this inside an existing repo
Copy this folder's contents into `ami-poc-demo/` at the repo root, and move
`.github/workflows/ami-poc.yml` to the REPO ROOT `.github/workflows/` (GitHub only runs
workflows from there). Paths in that workflow assume the `ami-poc-demo/` prefix.
Repository variables: `TF_STATE_BUCKET` (required), `AWS_ROLE_ARN` (OIDC) or the
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` secrets, optional `AWS_REGION`, `ALERT_EMAIL`.
Give the existing workflow a `paths:` filter so PoC pushes do not trigger it.
