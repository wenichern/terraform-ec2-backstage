"""Lambda subscribed to the ami-drift-alerts SNS topic (same topic the CloudWatch
alarm already notifies - see ami-poc-demo/terraform/detect-platform/main.tf and
ec2-demo/main.tf). Routes on the alarm's new state:
  ALARM -> open a Change Request (or reuse the existing open one - idempotent)
  OK    -> close the matching Change Request, if one is open

The alarm's `Service` dimension (the backstage-entity tag) becomes the ticket's
correlation_id, so open and close always find the same ticket, and a flapping
alarm doesn't spawn duplicates.
"""
import json
import logging

from servicenow_client import get_client

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _extract(sns_message: dict) -> dict:
    """Pull the fields we need out of a CloudWatch-alarm-via-SNS message."""
    dims = {d["name"]: d["value"] for d in sns_message.get("Trigger", {}).get("Dimensions", [])}
    return {
        "alarm_name": sns_message["AlarmName"],
        "new_state": sns_message["NewStateValue"],  # "ALARM" | "OK" | "INSUFFICIENT_DATA"
        "reason": sns_message.get("NewStateReason", ""),
        "service": dims.get("Service", "unknown"),
        "region": sns_message.get("Region", ""),
        "timestamp": sns_message.get("StateChangeTime", ""),
    }


def _handle_alarm(client, info: dict) -> dict:
    correlation_id = info["alarm_name"]
    short_desc = f"AMI update needed: {info['service']}"
    description = (
        f"CloudWatch alarm '{info['alarm_name']}' fired at {info['timestamp']}.\n"
        f"Service: {info['service']}\n"
        f"Reason: {info['reason']}\n\n"
        f"Ask mcp-chat 'is {info['service']} up to date' for current instance-level "
        f"detail, or run the golden path to apply the fix."
    )
    ticket = client.open_change_request(correlation_id, short_desc, description)
    logger.info("Opened/reused ticket %s for %s", ticket.number, correlation_id)
    return {"action": "opened", "ticket_number": ticket.number, "correlation_id": correlation_id}


def _handle_ok(client, info: dict) -> dict:
    correlation_id = info["alarm_name"]
    ticket = client.find_open_ticket(correlation_id)
    if not ticket:
        logger.info("No open ticket for %s; nothing to close", correlation_id)
        return {"action": "no_open_ticket", "correlation_id": correlation_id}
    close_notes = f"Alarm '{info['alarm_name']}' returned to OK at {info['timestamp']}. Auto-closed."
    client.close_ticket(ticket, close_notes)
    logger.info("Closed ticket %s for %s", ticket.number, correlation_id)
    return {"action": "closed", "ticket_number": ticket.number, "correlation_id": correlation_id}


def handler(event, context):
    client = get_client()
    results = []
    for record in event.get("Records", []):
        sns_message = json.loads(record["Sns"]["Message"])
        info = _extract(sns_message)

        if info["new_state"] == "ALARM":
            results.append(_handle_alarm(client, info))
        elif info["new_state"] == "OK":
            results.append(_handle_ok(client, info))
        else:
            logger.info("Ignoring state %s for %s", info["new_state"], info["alarm_name"])
            results.append({"action": "ignored", "state": info["new_state"]})

    return {"results": results}
EOF
echo written
Output

written
