"""Logins and guards for seeding a rep's Acctivity demo firm (see context/TECHNICAL_DESIGN.md → Demo environment).

Two logins reach the Corto services (Acctivity, Management, Docs Core, Dexter):
- the **service login**: a client-credentials token that acts as a firm and staff member named in headers;
- the **rep login**: the rep's own access token for their demo firm, handed over by the Windows-side tray-app helper
  (`Hack_A_API/tools/Send-RepLogin.ps1`) or pasted. Only the access token, kept in memory, never logged or stored.
  It is never refreshed here: the tray app owns the refresh token.

Callers (the seeder, the wipe) open a `DemoFirm(firm_id)` and call `.service(user_id)` or `.rep()` to get a
`FirmClient`. Every write (any method but GET) re-checks the guards first:
environment is test, the firm is on the allowlist, the rep token is current, from the test environment and for this
firm, and Management says the firm is a test firm. Nothing retries.
"""

import base64
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

SERVICES = {"acctivity": "ACCTIVITY_API_URL", "management": "ACCTIVITY_MANAGEMENT_API_URL",
            "docscore": "ACCTIVITY_DOCSCORE_API_URL", "dexter": "ACCTIVITY_DEXTER_API_URL"}
SERVICE_LOGIN_SETTINGS = ("ACCTIVITY_SERVICE_CLIENT_ID", "ACCTIVITY_SERVICE_CLIENT_SECRET",
                          "ACCTIVITY_SERVICE_TOKEN_URL", "ACCTIVITY_SERVICE_AUDIENCE")
TIMEOUT = 30
# Tokens are treated as expired this many seconds early, so none runs out mid-request.
EXPIRY_MARGIN = 60
REFRESH_HINT = "open the Acctivity tray app (test) to refresh"

# Tests replace these: httpx.MockTransport so nothing reaches Corto, and a fixed clock.
_transport = None
_now = time.time

_lock = threading.Lock()
_service_token: dict | None = None  # {"token", "expires_at"}
_rep_login: dict | None = None      # {"token", "source", "expires_at", "firm_id", "environment"}


class DemoLoginError(Exception):
    """A login or Corto call failed. `status_code` is what our API should answer with; `upstream_status` is
    the Corto service's HTTP status when there was one."""

    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None):
        super().__init__(message)
        self.status_code, self.upstream_status = status_code, upstream_status


class GuardRefused(DemoLoginError):
    """A firm guard refused the call; nothing was written."""

    def __init__(self, message: str):
        super().__init__(f"Refused: {message}", 403)


def reset() -> None:
    """Forget both logins (tests, and the rep's "forget" button)."""
    global _service_token, _rep_login
    with _lock:
        _service_token = _rep_login = None


# ---- service login ----

def service_login_configured() -> bool:
    return all(os.getenv(name) for name in SERVICE_LOGIN_SETTINGS)


def _get_service_token() -> str:
    global _service_token
    missing = [name for name in SERVICE_LOGIN_SETTINGS if not os.getenv(name)]
    if missing:
        raise DemoLoginError("Service login not configured. Set " + ", ".join(missing) + " in the server .env.", 503)
    with _lock:
        if _service_token and _now() < _service_token["expires_at"] - EXPIRY_MARGIN:
            return _service_token["token"]
        body = {"grant_type": "client_credentials", "client_id": os.environ["ACCTIVITY_SERVICE_CLIENT_ID"],
                "client_secret": os.environ["ACCTIVITY_SERVICE_CLIENT_SECRET"],
                "audience": os.environ["ACCTIVITY_SERVICE_AUDIENCE"]}
        try:
            with httpx.Client(timeout=TIMEOUT, transport=_transport) as client:
                response = client.post(os.environ["ACCTIVITY_SERVICE_TOKEN_URL"], json=body)
        except httpx.HTTPError:
            raise DemoLoginError("Could not reach the service login's token endpoint.", 502) from None
        if response.status_code >= 400:
            raise DemoLoginError(f"Service login refused (HTTP {response.status_code}). Check the service login "
                                 "settings in the server .env.", 502, response.status_code)
        data = response.json()
        _service_token = {"token": data["access_token"], "expires_at": _now() + int(data.get("expires_in", 0))}
        return _service_token["token"]


# ---- rep login ----

def _claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _claim(claims: dict, name: str):
    """Corto claims are namespaced (`https://<namespace>/firmId`); accept the bare name too."""
    for key, value in claims.items():
        if key == name or key.endswith("/" + name):
            return value
    return None


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def set_rep_token(token: str, source: Literal["tray", "pasted"]) -> dict:
    """Keep the rep's access token in memory (replacing any earlier one). Returns the login status."""
    global _rep_login
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    claims = _claims(token)
    firm_id, expires_at = _claim(claims, "firmId"), claims.get("exp")
    if not firm_id or not isinstance(expires_at, (int, float)):
        raise DemoLoginError("That is not an Acctivity access token (no firm id or expiry in it).", 422)
    if _now() >= expires_at - EXPIRY_MARGIN:
        raise DemoLoginError(f"That rep login has expired: {REFRESH_HINT}.", 422)
    with _lock:
        _rep_login = {"token": token, "source": source, "expires_at": expires_at, "firm_id": str(firm_id),
                      "environment": _claim(claims, "environment")}
    return rep_login_status()


def rep_login_status() -> dict:
    """found (from the tray app) / pasted / expired / missing, with expiry and firm id; never the token."""
    login = _rep_login
    if not login:
        return {"status": "missing", "source": None, "expires_at": None, "firm_id": None,
                "message": "No rep login. Run the tray-app helper on the host, or paste a token."}
    expired = _now() >= login["expires_at"] - EXPIRY_MARGIN
    status = "expired" if expired else ("found" if login["source"] == "tray" else "pasted")
    return {"status": status, "source": login["source"], "expires_at": _iso(login["expires_at"]),
            "firm_id": login["firm_id"],
            "message": f"The rep login has expired: {REFRESH_HINT}, then send it again." if expired else None}


def _current_rep_login() -> dict:
    login = _rep_login
    if not login:
        raise DemoLoginError("No rep login. Run the tray-app helper on the host, or paste a token.", 401)
    if _now() >= login["expires_at"] - EXPIRY_MARGIN:
        raise DemoLoginError(f"The rep login has expired: {REFRESH_HINT}, then send it again.", 401)
    return login


# ---- guarded firm session ----

def _allowlist() -> set[str]:
    return {item.strip().lower() for item in os.getenv("DEMO_FIRM_ALLOWLIST", "").split(",") if item.strip()}


def _environment() -> str:
    return os.getenv("DEMO_ENVIRONMENT", "").strip().lower()


class DemoFirm:
    """A session on one demo firm. Refuses up front if the environment or the allowlist says no; checks the
    remaining guards before every write."""

    def __init__(self, firm_id: str):
        self.firm_id = str(firm_id).strip()
        self._details: dict | None = None
        self._check_settings()

    def _check_settings(self) -> None:
        if _environment() != "test":
            raise GuardRefused("demo seeding only runs against the test environment (DEMO_ENVIRONMENT=test).")
        if self.firm_id.lower() not in _allowlist():
            raise GuardRefused("this firm is not on the demo firm allowlist (DEMO_FIRM_ALLOWLIST).")

    def details(self) -> dict:
        """The firm as Management reports it (`GET v1/firm`), read once per session."""
        if self._details is None:
            client = self.service() if service_login_configured() else self.rep()
            details = client.get("management", "v1/firm")
            self._details = details if isinstance(details, dict) else {}
        return self._details

    def check_writable(self) -> None:
        """Every guard; raises GuardRefused on the first that fails."""
        self._check_settings()
        try:
            login = _current_rep_login()
        except DemoLoginError as error:
            raise GuardRefused(f"writes need a current rep login. {error}") from None
        environment = str(login["environment"] or "").lower()
        if environment.rsplit("-", 1)[-1] != "test":
            raise GuardRefused("the rep login is not from the test environment.")
        if login["firm_id"].lower() != self.firm_id.lower():
            raise GuardRefused("the rep login belongs to another firm.")
        details = self.details()
        if str(details.get("firmId", "")).lower() != self.firm_id.lower():
            raise GuardRefused("Management returned a different firm.")
        if details.get("isTestFirm") is not True:
            raise GuardRefused("Management says this is not a test firm.")

    def service(self, user_id: str | None = None) -> "FirmClient":
        """The service login acting as this firm and (optionally) one of its staff."""
        return FirmClient(self, "service", user_id)

    def rep(self) -> "FirmClient":
        """The rep's own login for this firm."""
        return FirmClient(self, "rep")


class FirmClient:
    def __init__(self, firm: DemoFirm, login: Literal["service", "rep"], user_id: str | None = None):
        self.firm, self.login, self.user_id = firm, login, user_id

    def get(self, service: str, path: str, **kwargs):
        return self.request("GET", service, path, **kwargs)

    def post(self, service: str, path: str, **kwargs):
        return self.request("POST", service, path, **kwargs)

    def put(self, service: str, path: str, **kwargs):
        return self.request("PUT", service, path, **kwargs)

    def patch(self, service: str, path: str, **kwargs):
        return self.request("PATCH", service, path, **kwargs)

    def delete(self, service: str, path: str, **kwargs):
        return self.request("DELETE", service, path, **kwargs)

    def _headers(self, service: str, path: str) -> dict:
        if self.login == "rep":
            return {"Authorization": f"Bearer {_current_rep_login()['token']}"}
        firm_id = self.firm.firm_id
        headers = {"Authorization": f"Bearer {_get_service_token()}", "X-FirmId": firm_id, "X-Corto-FirmId": firm_id}
        if self.user_id:
            headers.update({"X-UserId": self.user_id, "X-Corto-UserId": self.user_id})
        if not (service == "management" and path == "v1/firm"):  # the firm read is where the country comes from
            country = self.firm.details().get("countryCode")
            if country:
                headers["X-CountryCode"] = str(country)
        return headers

    def request(self, method: str, service: str, path: str, **kwargs):
        """Send one request (`path` relative to the service's /api/ root, e.g. `v1/activities`). Returns the
        parsed JSON (None when empty). Raises DemoLoginError on any failure; never retries."""
        method = method.upper()
        if method != "GET":
            self.firm.check_writable()
        base = os.getenv(SERVICES.get(service, ""), "") if service in SERVICES else None
        if base is None:
            raise ValueError(f"Unknown service {service!r}")
        if not base:
            raise DemoLoginError(f"{SERVICES[service]} is not set in the server .env.", 503)
        headers = {"Accept": "application/json", **self._headers(service, path)}
        try:
            with httpx.Client(base_url=base.rstrip("/") + "/", timeout=TIMEOUT, transport=_transport) as client:
                response = client.request(method, path.lstrip("/"), headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise DemoLoginError(f"{service} {method} {path} timed out. Nothing was retried.", 504) from None
        except httpx.HTTPError:
            raise DemoLoginError(f"Could not reach {service}. Check the network or VPN.", 502) from None
        if response.status_code >= 400:
            hint = f" ({REFRESH_HINT})" if response.status_code == 401 and self.login == "rep" else ""
            raise DemoLoginError(f"{service} {method} {path} failed (HTTP {response.status_code}){hint}: "
                                 f"{response.text[:200]}", 502, response.status_code)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


# ---- API ----

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class RepLogin(BaseModel):
    access_token: str
    source: Literal["tray", "pasted"] = "pasted"


@router.get("/rep-login")
def get_rep_login() -> dict:
    return rep_login_status()


@router.put("/rep-login")
def put_rep_login(body: RepLogin) -> dict:
    try:
        return set_rep_token(body.access_token, body.source)
    except DemoLoginError as error:
        raise HTTPException(error.status_code, str(error)) from None


@router.delete("/rep-login")
def delete_rep_login() -> dict:
    global _rep_login
    with _lock:
        _rep_login = None
    return rep_login_status()


def _read(client: FirmClient) -> dict:
    """Read-only smoke check: the firm and its users through one login."""
    result = {}
    try:
        firm = client.get("management", "v1/firm") or {}
        result["firm"] = {"status": 200, "firm_name": firm.get("firmName"), "is_test_firm": firm.get("isTestFirm")}
    except DemoLoginError as error:
        result["firm"] = {"status": error.upstream_status, "error": str(error)}
    try:
        users = client.get("management", "v1/users") or []
        result["users"] = {"status": 200, "count": len(users)}
    except DemoLoginError as error:
        result["users"] = {"status": error.upstream_status, "error": str(error)}
    return result


@router.get("/firms/{firm_id}/check")
def check_firm(firm_id: str) -> dict:
    """Read-only: reads the firm and its users with both logins and reports whether writes would be allowed."""
    try:
        firm = DemoFirm(firm_id)
    except GuardRefused as error:
        return {"firm_id": firm_id, "writable": False, "refused": str(error), "reads": {},
                "rep_login": rep_login_status()}
    reads = {"service": _read(firm.service()), "rep": _read(firm.rep())}
    try:
        firm.check_writable()
        refused = None
    except DemoLoginError as error:
        refused = str(error)
    return {"firm_id": firm_id, "writable": refused is None, "refused": refused, "reads": reads,
            "rep_login": rep_login_status()}
