# Migrating ec2-demo from a single aws_instance to an ASG + launch template

## What changed and why
- `aws_instance.app` -> `aws_launch_template.app` + `aws_autoscaling_group.app`.
  The "fix" is now: change `ami_id` -> new launch template version -> ASG instance
  refresh rolls the fleet keeping `min_healthy_percentage = 90` throughout. No more
  single-instance replace-in-place downtime.
- Added `aws_security_group.app`: purpose-built, no inbound, instead of relying on the
  VPC default SG.
- The alarm now has ONLY a `Service` dimension (was `InstanceId` + `Service`), because
  an ASG has no single instance to key an alarm on — it fires if ANY instance in the
  group is outdated.
- `ami_checker.py publish()` now emits a second, per-service aggregate metric (Service
  dimension only, MAX across instances) alongside the existing per-instance metrics, so
  the ASG-level alarm has a matching dimension set to read. Per-instance metrics are
  kept too — that's what a future `list_outdated_ec2` chat tool and investigation will
  use to say WHICH instance, not just which service.

## This is a replace, not an in-place upgrade of existing state
Terraform cannot convert an `aws_instance` into an ASG in place — the resource types are
different. Applying this will DESTROY the existing PoC instance/alarm and CREATE a new
launch template, security group, ASG, and alarm.

If you still have the single-instance PoC deployed, tear it down first:
  Actions tab -> ami-poc-destroy -> Run workflow -> type "destroy"
Then apply this version fresh (push to `ami-poc-demo/terraform/ec2-demo/main.tf`, or
use the existing `ami-poc.yml` deploy workflow — no workflow changes needed, since the
variables (`ami_id`, `alarm_topic_arn`, etc.) and outputs stayed compatible; only
`instance_id`/`ami_id` outputs were replaced with `asg_name`/`launch_template_id`, so
any downstream code reading `terraform output instance_id` needs updating).

## New variables (all optional except ami_id)
| Variable | Default | Notes |
|---|---|---|
| `desired_capacity` | 1 | |
| `min_size` | 1 | |
| `max_size` | 2 | Must be > min_size so instance refresh has room to roll one out at a time |

## Testing the fix after this change
```bash
# old ami_id already in tfvars -> apply -> checker reports drift -> alarm fires
# to test the FIX:
terraform apply -var ami_id=<new-ami-id>       # new launch template version
aws autoscaling start-instance-refresh --auto-scaling-group-name ami-poc-demo
# instance refresh also runs automatically as part of `terraform apply` via the
# instance_refresh block, no separate command needed in normal use — shown above
# only if you want to trigger a refresh without a new ami_id (e.g. testing rollback).
```

## What's still NOT done
- CI/CD workflow (`ami-poc.yml`) needs no changes to keep working, but hasn't been
  re-tested against this file — recommend a fresh `terraform plan` review before apply.
- `list_outdated_ec2` MCP tool: still pending (per-instance metrics are ready for it).
- Ticket automation (open/close Lambdas on the SNS topic): still pending.
