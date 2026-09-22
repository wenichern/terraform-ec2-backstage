"""MCP server exposing AMI-drift status as a read-only chat tool.

Wraps the same ami_checker.py used by the Lambda/CloudWatch pipeline, so the
chat's answer always matches what triggers alarms and tickets - one source of
truth for what "outdated" means.
"""
import os

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

import ami_checker as ac

REGION = os.getenv("AWS_REGION", "us-east-1")
SSM_PARAM = os.getenv("LATEST_AMI_SSM_PARAM", ac.DEFAULT_SSM_PARAM)
HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("BRIDGE_PORT", "9002"))

mcp = FastMCP("aws-ops")


def _row(r: dict) -> str:
    return (f"- {r['instance_id']} ({r['service'] or 'untagged'}): {r['status']} - {r['reason']}. "
            f"current={r['current_ami']} ({r['current_ami_name']}) latest={r['latest_ami']}")


@mcp.tool()
async def list_outdated_ec2(service: str = "", region: str = "") -> str:
    """List running EC2 instances whose AMI is out of date, deprecated, or missing.
    Optionally filter by service (the backstage-entity tag, e.g. 'component:default/payments-api').
    Use this to answer questions like "which EC2 need an AMI update" or "is <service> up to date".
    Only reports; does not change anything.

    IMPORTANT: if a service filter matches zero instances, the result starts with
    "UNKNOWN" - this means the service has no running, tagged instances (e.g. wrong name,
    not deployed, or not yet tagged). This is NOT the same as being up to date and must
    never be reported to the user as healthy, current, or up to date. Relay the UNKNOWN
    result and its explanation as-is."""
    try:
        results = ac.scan(region or REGION, SSM_PARAM)
    except Exception as e:
        raise ToolError(f"Could not scan EC2/AMI state: {e}")

    if service:
        results = [r for r in results if r["service"] == service]
        if not results:
            return (f"UNKNOWN - no data for service '{service}'. "
                     "No running instances are tagged with this service name, so its "
                     "AMI status cannot be determined. Do NOT report this as up to date "
                     "or healthy. Check the service name is correct, or that it has any "
                     "running, tagged instances.")

    outdated = [r for r in results if r["status"] != "ok"]
    if not outdated:
        scope = f" for {service}" if service else ""
        return f"UP TO DATE - All {len(results)} scanned instance(s){scope} are on the latest AMI."

    lines = [f"{len(outdated)} of {len(results)} instance(s) need an AMI update:"]
    lines += [_row(r) for r in outdated]
    return "\n".join(lines)


@mcp.tool()
async def check_ec2_ami(instance_id: str, region: str = "") -> str:
    """Report the AMI status of one specific EC2 instance by instance ID."""
    try:
        results = ac.scan(region or REGION, SSM_PARAM)
    except Exception as e:
        raise ToolError(f"Could not scan EC2/AMI state: {e}")

    for r in results:
        if r["instance_id"] == instance_id:
            return _row(r)
    return f"Instance {instance_id} not found among running, tagged instances in {region or REGION}."


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=HOST, port=PORT, path="/mcp")
