"""Thin Sendspark API client for the personalised AI video output (see context/SENDSPARK.md).

The base video and its variables are set up once in the Sendspark app as a dynamic campaign
(SENDSPARK_DYNAMIC_ID); the API can only add prospects to it. Adding a prospect uses video minutes,
so nothing here retries. Key and secret are only ever sent to Sendspark, never returned.
"""

import os
import re

import httpx

BASE_URL = "https://api-gw.sendspark.com/v1"
TIMEOUT = 30
SETTINGS = ("SENDSPARK_API_KEY", "SENDSPARK_API_SECRET_KEY", "SENDSPARK_WORKSPACE_ID",
            "SENDSPARK_DYNAMIC_ID", "SENDSPARK_PROSPECT_EMAIL")
NOT_CONFIGURED = "Sendspark not configured. Set " + ", ".join(SETTINGS) + " in the server .env."
DONE, FAILED = "completed", "errored"

# Tests replace this with httpx.MockTransport so no request reaches Sendspark.
_transport = None


class SendsparkError(Exception):
    """A Sendspark call failed. `status_code` is the HTTP status our API should answer with."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def configured() -> bool:
    return all(os.getenv(name) for name in SETTINGS)


def prospect_email(domain: str) -> str:
    """Sendspark identifies prospects by email, so each firm gets its own plus-address on our inbox."""
    local, _, host = os.environ["SENDSPARK_PROSPECT_EMAIL"].partition("@")
    return f"{local}+{re.sub(r'[^a-z0-9-]', '-', domain.lower())}@{host}"


def _campaign_path() -> str:
    return f"/workspaces/{os.environ['SENDSPARK_WORKSPACE_ID']}/dynamics/{os.environ['SENDSPARK_DYNAMIC_ID']}"


def _request(method: str, path: str, **kwargs) -> dict:
    if not configured():
        raise SendsparkError(NOT_CONFIGURED, 503)
    headers = {"x-api-key": os.environ["SENDSPARK_API_KEY"], "x-api-secret": os.environ["SENDSPARK_API_SECRET_KEY"],
               "Accept": "application/json"}
    try:
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=headers, transport=_transport) as client:
            response = client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        raise SendsparkError("Sendspark did not respond in time. Nothing was retried; try again.", 504) from None
    except httpx.HTTPError:
        raise SendsparkError("Could not reach Sendspark. Check the server's network connection.", 502) from None
    if response.status_code == 401:
        raise SendsparkError("Sendspark rejected the API key or secret (401). Check the server .env.", 502)
    if response.status_code == 404:
        raise SendsparkError("Sendspark could not find that campaign or prospect (404).", 404)
    if response.status_code == 429:
        raise SendsparkError("Sendspark rate limit reached (429). Wait a minute and try again.", 429)
    if response.status_code >= 400:
        raise SendsparkError(f"Sendspark request failed (HTTP {response.status_code}).", 502)
    try:
        data = response.json()
    except ValueError:
        raise SendsparkError("Sendspark returned a response that is not JSON.", 502) from None
    # Sendspark wraps some payloads as {"response": {"data": ...}}.
    if isinstance(data, dict) and isinstance(data.get("response"), dict):
        data = data["response"]
    return data.get("data", data) if isinstance(data, dict) else data


def add_prospect(firm_name: str, email: str, website: str) -> dict:
    """Queue one personalised video (uses video minutes). The firm name fills both the name and company
    variables. Sendspark only renders when processAndAuthorizeCharge is true (false just saves the
    prospect); it also accepts charges for videos beyond the plan's allowance."""
    return _request("POST", f"{_campaign_path()}/prospect", json={
        "prospect": {"contactName": firm_name, "company": firm_name, "contactEmail": email,
                     "backgroundUrl": website},
        "processAndAuthorizeCharge": True})


def get_prospect(email: str) -> dict:
    """The prospect's current state (free): `status` is saved, billed, processing, uploaded, completed or
    errored; `shareUrl` is the video's public page once completed."""
    return _request("GET", f"{_campaign_path()}/prospects/{email}")
