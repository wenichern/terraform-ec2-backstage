import json

import pytest

from servicenow_client import MockServiceNowClient
import lambda_handler as lh


# ---- ServiceNowClient (mock) behavior ---------------------------------------

def test_open_is_idempotent():
    c = MockServiceNowClient()
    t1 = c.open_change_request("ami-drift-payments-api", "short", "desc")
    t2 = c.open_change_request("ami-drift-payments-api", "short", "desc")
    assert t1.sys_id == t2.sys_id  # same ticket, not a duplicate
    assert t1.state == "open"


def test_close_then_reopen_creates_new_ticket():
    c = MockServiceNowClient()
    t1 = c.open_change_request("ami-drift-x", "s", "d")
    c.close_ticket(t1, "fixed")
    assert c.find_open_ticket("ami-drift-x") is None  # closed, no longer "open"

    t2 = c.open_change_request("ami-drift-x", "s", "d")
    assert t2.sys_id != t1.sys_id  # a genuinely new incident gets a new ticket


def test_find_open_ticket_none_when_never_opened():
    c = MockServiceNowClient()
    assert c.find_open_ticket("never-seen") is None


# ---- Lambda handler: realistic SNS(CloudWatch alarm) payloads --------------

def _sns_event(alarm_name, new_state, service, reason="Threshold Crossed"):
    message = {
        "AlarmName": alarm_name,
        "NewStateValue": new_state,
        "NewStateReason": reason,
        "Region": "US East (N. Virginia)",
        "StateChangeTime": "2026-09-22T10:00:00.000+0000",
        "Trigger": {"Dimensions": [{"name": "Service", "value": service}]},
    }
    return {"Records": [{"Sns": {"Message": json.dumps(message)}}]}


def test_alarm_opens_ticket(monkeypatch):
    monkeypatch.setenv("_unused", "1")  # ensure no SERVICENOW_INSTANCE_URL -> mock client
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)

    event = _sns_event("ami-drift-payments-api", "ALARM", "component:default/payments-api")
    out = lh.handler(event, None)

    assert out["results"][0]["action"] == "opened"
    assert out["results"][0]["correlation_id"] == "ami-drift-payments-api"


def test_alarm_then_ok_closes_the_same_ticket(monkeypatch):
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)

    # Same client instance must be reused across calls for this test to be meaningful -
    # patch get_client to return one shared mock, since the real handler calls
    # get_client() fresh each invocation (matching real Lambda cold/warm start behavior,
    # where a real ServiceNow client would hit the same persistent backend regardless).
    shared = MockServiceNowClient()
    monkeypatch.setattr(lh, "get_client", lambda: shared)

    alarm_event = _sns_event("ami-drift-checkout", "ALARM", "component:default/checkout")
    ok_event = _sns_event("ami-drift-checkout", "OK", "component:default/checkout")

    open_result = lh.handler(alarm_event, None)["results"][0]
    assert open_result["action"] == "opened"

    close_result = lh.handler(ok_event, None)["results"][0]
    assert close_result["action"] == "closed"
    assert close_result["ticket_number"] == open_result["ticket_number"]  # same ticket


def test_ok_with_no_open_ticket_does_not_error(monkeypatch):
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)
    shared = MockServiceNowClient()
    monkeypatch.setattr(lh, "get_client", lambda: shared)

    ok_event = _sns_event("ami-drift-never-alarmed", "OK", "component:default/x")
    out = lh.handler(ok_event, None)

    assert out["results"][0]["action"] == "no_open_ticket"


def test_insufficient_data_is_ignored(monkeypatch):
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)
    event = _sns_event("ami-drift-new-instance", "INSUFFICIENT_DATA", "component:default/y")
    out = lh.handler(event, None)
    assert out["results"][0]["action"] == "ignored"


def test_multiple_records_in_one_invocation(monkeypatch):
    """SNS can batch multiple notifications into one Lambda event."""
    monkeypatch.delenv("SERVICENOW_INSTANCE_URL", raising=False)
    event = {
        "Records": (
            _sns_event("ami-drift-a", "ALARM", "component:default/a")["Records"]
            + _sns_event("ami-drift-b", "ALARM", "component:default/b")["Records"]
        )
    }
    out = lh.handler(event, None)
    assert len(out["results"]) == 2
    assert {r["correlation_id"] for r in out["results"]} == {"ami-drift-a", "ami-drift-b"}
