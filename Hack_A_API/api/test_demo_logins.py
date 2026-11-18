import base64
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import demo_logins

FIRM = "firm-1111"
OTHER_FIRM = "firm-2222"
STAFF = "user-3333"
NOW = 1_800_000_000

SETTINGS = {
    "DEMO_ENVIRONMENT": "test",
    "DEMO_FIRM_ALLOWLIST": f"{FIRM}, firm-9999",
    "ACCTIVITY_API_URL": "https://acctivity.test.invalid/api/",
    "ACCTIVITY_MANAGEMENT_API_URL": "https://management.test.invalid/api/",
    "ACCTIVITY_DOCSCORE_API_URL": "https://docscore.test.invalid/api/",
    "ACCTIVITY_DEXTER_API_URL": "https://dexter.test.invalid/api/",
    "ACCTIVITY_SERVICE_CLIENT_ID": "client-id",
    "ACCTIVITY_SERVICE_CLIENT_SECRET": "client-secret",
    "ACCTIVITY_SERVICE_TOKEN_URL": "https://auth.test.invalid/oauth/token",
    "ACCTIVITY_SERVICE_AUDIENCE": "https://audience.test.invalid",
}


def jwt(**claims) -> str:
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


CLAIMS = "https://claims.test.invalid/"


def rep_jwt(firm=FIRM, exp=NOW + 3600, environment="example-test", **extra) -> str:
    """Shaped like a real rep token: namespaced Corto claims, numeric country code, permissions array."""
    claims = {"exp": exp, "iat": exp - 86400, "aud": ["https://audience.test.invalid"],
              "scope": "openid profile email offline_access", "permissions": ["write:firm", "manage:users"],
              CLAIMS + "countryCode": 5, CLAIMS + "environment": environment, **extra}
    if firm:
        claims[CLAIMS + "firmId"] = firm
    return jwt(**claims)


REP_TOKEN = rep_jwt()


class FakeCorto:
    """Stands in for Auth0 and the four Corto services: records every request, answers from simple state."""

    def __init__(self, is_test_firm=True, firm_id=FIRM, write_status=201):
        self.is_test_firm, self.firm_id, self.write_status = is_test_firm, firm_id, write_status
        self.requests, self.issued = [], 0
        self.clock = NOW

    def __enter__(self):
        self.patches = [patch.object(demo_logins, "_transport", httpx.MockTransport(self.handle)),
                        patch.object(demo_logins, "_now", lambda: self.clock),
                        patch.dict(os.environ, SETTINGS)]
        for p in self.patches:
            p.start()
        demo_logins.reset()
        return self

    def __exit__(self, *exc):
        demo_logins.reset()
        for p in reversed(self.patches):
            p.stop()

    def handle(self, request):
        self.requests.append(request)
        if request.url.host == "auth.test.invalid":
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"service-token-{self.issued}",
                                             "expires_in": 3600, "token_type": "Bearer"})
        if request.method == "GET" and request.url.path == "/api/v1/firm":
            return httpx.Response(200, json={"firmId": self.firm_id, "firmName": "Demo Firm",
                                             "isTestFirm": self.is_test_firm, "countryCode": "NZ"})
        if request.method == "GET" and request.url.path == "/api/v1/users":
            return httpx.Response(200, json=[{"userId": STAFF, "firstName": "Sam"}])
        return httpx.Response(self.write_status, json={"id": "created-1"})

    def writes(self):
        return [r for r in self.requests if r.method != "GET" and r.url.host != "auth.test.invalid"]


class ServiceLoginTests(unittest.TestCase):
    def test_service_token_is_cached_until_shortly_before_expiry(self):
        with FakeCorto() as fake:
            client = demo_logins.DemoFirm(FIRM).service(user_id=STAFF)
            client.get("management", "v1/users")
            client.get("management", "v1/users")
            self.assertEqual(fake.issued, 1)
            token_request = json.loads(fake.requests[0].content)
            self.assertEqual(token_request, {"grant_type": "client_credentials", "client_id": "client-id",
                                             "client_secret": "client-secret",
                                             "audience": "https://audience.test.invalid"})
            fake.clock = NOW + 3600 - 30  # inside the safety margin: fetch a new one
            client.get("management", "v1/users")
            self.assertEqual(fake.issued, 2)

    def test_service_login_acts_as_the_firm_and_staff_member(self):
        with FakeCorto() as fake:
            demo_logins.DemoFirm(FIRM).service(user_id=STAFF).get("dexter", "v1/lookups", params={"x": "1"})
        call = fake.requests[-1]
        self.assertEqual(str(call.url), "https://dexter.test.invalid/api/v1/lookups?x=1")
        self.assertEqual(call.headers["authorization"], "Bearer service-token-1")
        for header in ("x-firmid", "x-corto-firmid"):
            self.assertEqual(call.headers[header], FIRM)
        for header in ("x-userid", "x-corto-userid"):
            self.assertEqual(call.headers[header], STAFF)
        self.assertEqual(call.headers["x-countrycode"], "NZ")  # from the firm's own details

    def test_missing_service_settings_are_named(self):
        with FakeCorto(), patch.dict(os.environ, {"ACCTIVITY_SERVICE_CLIENT_SECRET": ""}):
            with self.assertRaises(demo_logins.DemoLoginError) as error:
                demo_logins.DemoFirm(FIRM).service().get("management", "v1/firm")
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("ACCTIVITY_SERVICE_CLIENT_SECRET", str(error.exception))

    def test_upstream_4xx_is_reported_once_without_retry(self):
        with FakeCorto(write_status=400) as fake:
            demo_logins.set_rep_token(REP_TOKEN, "pasted")
            with self.assertRaises(demo_logins.DemoLoginError) as error:
                demo_logins.DemoFirm(FIRM).service(user_id=STAFF).post("acctivity", "v1/activities", json={})
        self.assertEqual(len(fake.writes()), 1)
        self.assertEqual(error.exception.upstream_status, 400)


class RepLoginTests(unittest.TestCase):
    def test_rep_login_status_reports_expiry_but_never_the_token(self):
        with FakeCorto():
            self.assertEqual(demo_logins.rep_login_status()["status"], "missing")
            status = demo_logins.set_rep_token(REP_TOKEN, "tray")
            self.assertEqual((status["status"], status["firm_id"]), ("found", FIRM))
            self.assertEqual(status["expires_at"], "2027-01-15T09:00:00+00:00")
            self.assertNotIn(REP_TOKEN, json.dumps(status))
            self.assertEqual(demo_logins.set_rep_token(REP_TOKEN, "pasted")["status"], "pasted")

    def test_expired_rep_token_asks_for_the_tray_app(self):
        with FakeCorto() as fake:
            demo_logins.set_rep_token(REP_TOKEN, "tray")
            fake.clock = NOW + 3600
            status = demo_logins.rep_login_status()
            self.assertEqual(status["status"], "expired")
            self.assertIn("open the Acctivity tray app (test) to refresh", status["message"])
            with self.assertRaises(demo_logins.DemoLoginError) as error:
                demo_logins.DemoFirm(FIRM).rep().get("docscore", "v1/precedent-tree/matter-types")
            self.assertIn("open the Acctivity tray app (test) to refresh", str(error.exception))
            with self.assertRaises(demo_logins.DemoLoginError) as error:
                demo_logins.set_rep_token(rep_jwt(exp=NOW), "tray")
            self.assertIn("open the Acctivity tray app (test) to refresh", str(error.exception))
        self.assertEqual([r for r in fake.requests if r.url.host == "docscore.test.invalid"], [])

    def test_rep_token_without_a_firm_is_refused(self):
        with FakeCorto():
            for bad in ("not-a-jwt", rep_jwt(firm=None)):
                with self.assertRaises(demo_logins.DemoLoginError):
                    demo_logins.set_rep_token(bad, "pasted")
            self.assertEqual(demo_logins.rep_login_status()["status"], "missing")

    def test_rep_login_sends_only_the_bearer_token(self):
        with FakeCorto() as fake:
            demo_logins.set_rep_token(REP_TOKEN, "pasted")
            demo_logins.DemoFirm(FIRM).rep().get("docscore", "v1/precedent-tree/matter-types")
        call = fake.requests[-1]
        self.assertEqual(call.headers["authorization"], f"Bearer {REP_TOKEN}")
        self.assertNotIn("x-firmid", call.headers)
        self.assertNotIn("x-userid", call.headers)


class GuardTests(unittest.TestCase):
    def assert_write_refused(self, fake, message, firm=FIRM):
        with self.assertRaises(demo_logins.GuardRefused) as error:
            demo_logins.DemoFirm(firm).service(user_id=STAFF).post("acctivity", "v1/activities", json={})
        self.assertEqual(error.exception.status_code, 403)
        self.assertIn(message, str(error.exception))
        self.assertEqual(fake.writes(), [])

    def test_write_passes_all_guards_and_checks_the_firm_once(self):
        with FakeCorto() as fake:
            demo_logins.set_rep_token(REP_TOKEN, "tray")
            firm = demo_logins.DemoFirm(FIRM)
            firm.service(user_id=STAFF).post("acctivity", "v1/activities", json={"a": 1})
            firm.rep().patch("management", "v1/firm/name", json={"firmName": "X"})
        self.assertEqual(len(fake.writes()), 2)
        self.assertEqual(len([r for r in fake.requests if r.url.path == "/api/v1/firm"]), 1)

    def test_refuses_outside_the_test_environment(self):
        with FakeCorto() as fake, patch.dict(os.environ, {"DEMO_ENVIRONMENT": "live"}):
            demo_logins.set_rep_token(REP_TOKEN, "tray")
            self.assert_write_refused(fake, "test environment")

    def test_refuses_a_firm_not_on_the_allowlist(self):
        with FakeCorto(firm_id=OTHER_FIRM) as fake:
            demo_logins.set_rep_token(rep_jwt(OTHER_FIRM), "tray")
            self.assert_write_refused(fake, "allowlist", firm=OTHER_FIRM)

    def test_refuses_a_firm_that_is_not_a_test_firm(self):
        with FakeCorto(is_test_firm=False) as fake:
            demo_logins.set_rep_token(REP_TOKEN, "tray")
            self.assert_write_refused(fake, "not a test firm")

    def test_refuses_when_the_rep_token_is_for_another_firm(self):
        with FakeCorto() as fake:
            demo_logins.set_rep_token(rep_jwt(OTHER_FIRM), "tray")
            self.assert_write_refused(fake, "another firm")

    def test_refuses_a_rep_token_from_another_environment(self):
        with FakeCorto() as fake:
            demo_logins.set_rep_token(rep_jwt(environment="example-live"), "tray")
            self.assert_write_refused(fake, "environment")

    def test_refuses_without_a_rep_login(self):
        with FakeCorto() as fake:
            self.assert_write_refused(fake, "rep login")

    def test_allowlist_is_checked_again_on_every_write(self):
        with FakeCorto() as fake:
            demo_logins.set_rep_token(REP_TOKEN, "tray")
            client = demo_logins.DemoFirm(FIRM).service(user_id=STAFF)
            client.post("acctivity", "v1/activities", json={})
            with patch.dict(os.environ, {"DEMO_FIRM_ALLOWLIST": "firm-9999"}):
                with self.assertRaises(demo_logins.GuardRefused):
                    client.post("acctivity", "v1/activities", json={})
        self.assertEqual(len(fake.writes()), 1)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(demo_logins.router)
        self.api = TestClient(app)

    def test_rep_login_can_be_handed_over_checked_and_forgotten(self):
        with FakeCorto():
            self.assertEqual(self.api.get("/api/v1/demo/rep-login").json()["status"], "missing")
            put = self.api.put("/api/v1/demo/rep-login", json={"access_token": REP_TOKEN, "source": "tray"})
            self.assertEqual(put.status_code, 200)
            self.assertNotIn(REP_TOKEN, put.text)
            status = self.api.get("/api/v1/demo/rep-login")
            self.assertEqual(status.json()["status"], "found")
            self.assertNotIn(REP_TOKEN, status.text)
            bad = self.api.put("/api/v1/demo/rep-login", json={"access_token": "nope", "source": "pasted"})
            self.assertEqual(bad.status_code, 422)
            self.assertNotIn("nope", bad.text)
            self.api.delete("/api/v1/demo/rep-login")
            self.assertEqual(self.api.get("/api/v1/demo/rep-login").json()["status"], "missing")

    def test_check_reads_the_firm_and_users_with_both_logins(self):
        with FakeCorto() as fake:
            self.api.put("/api/v1/demo/rep-login", json={"access_token": REP_TOKEN, "source": "pasted"})
            report = self.api.get(f"/api/v1/demo/firms/{FIRM}/check").json()
        self.assertTrue(report["writable"])
        for login in ("service", "rep"):
            self.assertEqual(report["reads"][login]["firm"], {"status": 200, "firm_name": "Demo Firm",
                                                              "is_test_firm": True})
            self.assertEqual(report["reads"][login]["users"], {"status": 200, "count": 1})
        self.assertEqual(fake.writes(), [])
        self.assertNotIn(REP_TOKEN, json.dumps(report))

    def test_check_explains_a_refused_firm_without_calling_out(self):
        with FakeCorto() as fake:
            report = self.api.get(f"/api/v1/demo/firms/{OTHER_FIRM}/check").json()
        self.assertFalse(report["writable"])
        self.assertIn("allowlist", report["refused"])
        self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()
