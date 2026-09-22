"""ServiceNow client interface + two implementations:
- MockServiceNowClient: in-memory, for local dev and tests, no network.
- RealServiceNowClient: Table API over HTTPS, for use once a real instance exists.

Both implement the same interface so lambda_handler.py never has to know which
one it's talking to - swapping mock for real is a config change, not a code change.

Idempotency: every ticket is tagged with a `correlation_id` (the CloudWatch alarm
name, e.g. "ami-drift-payments-api"). Before opening a new ticket we look for an
existing OPEN one with the same correlation_id, so a flapping alarm or a retried
Lambda invocation doesn't create duplicates. Closing looks up the ticket the same way.
"""
import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass, field


@dataclass
class Ticket:
    sys_id: str
    number: str
    correlation_id: str
    state: str  # "open" | "closed"
    short_description: str = ""
    description: str = ""


class ServiceNowClient:
    """Interface both implementations follow."""

    def find_open_ticket(self, correlation_id: str) -> "Ticket | None":
        raise NotImplementedError

    def open_change_request(self, correlation_id: str, short_description: str,
                             description: str) -> Ticket:
        raise NotImplementedError

    def close_ticket(self, ticket: Ticket, close_notes: str) -> Ticket:
        raise NotImplementedError


class MockServiceNowClient(ServiceNowClient):
    """In-memory store. Same behavior contract as the real client, so tests
    against this mock exercise the real idempotency/lookup logic."""

    def __init__(self):
        self._tickets: dict[str, Ticket] = {}  # keyed by correlation_id
        self._counter = 0

    def find_open_ticket(self, correlation_id: str) -> "Ticket | None":
        t = self._tickets.get(correlation_id)
        return t if t and t.state == "open" else None

    def open_change_request(self, correlation_id: str, short_description: str,
                             description: str) -> Ticket:
        existing = self.find_open_ticket(correlation_id)
        if existing:
            return existing  # idempotent: don't duplicate
        self._counter += 1
        t = Ticket(sys_id=f"mock-sys-{self._counter}", number=f"CHG{self._counter:07d}",
                   correlation_id=correlation_id, state="open",
                   short_description=short_description, description=description)
        self._tickets[correlation_id] = t
        return t

    def close_ticket(self, ticket: Ticket, close_notes: str) -> Ticket:
        t = self._tickets.get(ticket.correlation_id)
        if t:
            t.state = "closed"
            t.description += f"\n\n--- Closed ---\n{close_notes}"
        return t or ticket


class RealServiceNowClient(ServiceNowClient):
    """ServiceNow Table API (change_request table), Basic Auth.
    UNTESTED against a live instance - no PDI was available while this was built.
    Verify field names (state values, table name) against your instance's schema
    before relying on this in production; PDI defaults can differ by release."""

    def __init__(self, instance_url: str | None = None, user: str | None = None,
                 password: str | None = None, table: str = "change_request"):
        self.base = (instance_url or os.environ["SERVICENOW_INSTANCE_URL"]).rstrip("/")
        self.user = user or os.environ["SERVICENOW_USER"]
        self.password = password or os.environ["SERVICENOW_PASSWORD"]
        self.table = table

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        import base64
        url = f"{self.base}/api/now/table/{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        auth = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ServiceNow API error {e.code}: {e.read().decode()[:500]}")

    def find_open_ticket(self, correlation_id: str) -> "Ticket | None":
        query = f"correlation_id={correlation_id}^state!=3^state!=4"  # not Closed/Cancelled
        result = self._request("GET", f"{self.table}?sysparm_query={query}&sysparm_limit=1")
        records = result.get("result", [])
        if not records:
            return None
        r = records[0]
        return Ticket(sys_id=r["sys_id"], number=r["number"], correlation_id=correlation_id,
                      state="open", short_description=r.get("short_description", ""),
                      description=r.get("description", ""))

    def open_change_request(self, correlation_id: str, short_description: str,
                             description: str) -> Ticket:
        existing = self.find_open_ticket(correlation_id)
        if existing:
            return existing
        body = {
            "correlation_id": correlation_id,
            "short_description": short_description,
            "description": description,
            "type": "standard",  # pre-approved/low-risk change category; verify against your instance
        }
        result = self._request("POST", self.table, body)
        r = result["result"]
        return Ticket(sys_id=r["sys_id"], number=r["number"], correlation_id=correlation_id,
                      state="open", short_description=short_description, description=description)

    def close_ticket(self, ticket: Ticket, close_notes: str) -> Ticket:
        body = {"state": "3", "close_notes": close_notes, "close_code": "Successful"}
        self._request("PATCH", f"{self.table}/{ticket.sys_id}", body)
        ticket.state = "closed"
        return ticket


def get_client() -> ServiceNowClient:
    """Factory: real client if SERVICENOW_INSTANCE_URL is set, mock otherwise.
    This is the single switch to flip once a real PDI exists - no other code changes."""
    if os.environ.get("SERVICENOW_INSTANCE_URL"):
        return RealServiceNowClient()
    return MockServiceNowClient()
