"""Thin Apollo API client. Optional: every call needs APOLLO_API_KEY in the server environment.

Credit costs (see context/APOLLO.md): enrich_org 1, search_people 0, reveal 1 per matched person
(no phones requested), search_orgs 1 per page. Nothing here retries, so one call is at most one charge.
The key is only ever sent to Apollo; it never appears in responses or error messages.
"""

import os

import httpx

BASE_URL = "https://api.apollo.io/api/v1"
TIMEOUT = 30
MAX_REVEAL = 10
NOT_CONFIGURED = "Apollo not configured. Set APOLLO_API_KEY in the server .env to enable it."

# Tests replace this with httpx.MockTransport so no request reaches Apollo.
_transport = None


class ApolloError(Exception):
    """An Apollo call failed. `status_code` is the HTTP status our API should answer with."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def configured() -> bool:
    return bool(os.getenv("APOLLO_API_KEY"))


def _request(method: str, path: str, **kwargs) -> dict:
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        raise ApolloError(NOT_CONFIGURED, 503)
    headers = {"x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"}
    try:
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=headers, transport=_transport) as client:
            response = client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        raise ApolloError("Apollo did not respond in time. Nothing was retried; try again.", 504) from None
    except httpx.HTTPError:
        raise ApolloError("Could not reach Apollo. Check the server's network connection.", 502) from None
    if response.status_code == 401:
        raise ApolloError("Apollo rejected the API key (401). Check APOLLO_API_KEY in the server .env.", 502)
    if response.status_code == 403:
        raise ApolloError("Apollo refused this request (403). The API key or plan does not include this "
                          "endpoint; use a master key or a plan with API access.", 502)
    if response.status_code == 429:
        raise ApolloError("Apollo rate limit reached (429). Wait a minute and try again; "
                          "nothing was retried automatically.", 429)
    if response.status_code >= 400:
        raise ApolloError(f"Apollo request failed (HTTP {response.status_code}).", 502)
    try:
        return response.json()
    except ValueError:
        raise ApolloError("Apollo returned a response that is not JSON.", 502) from None


def enrich_org(domain: str) -> dict | None:
    """Organization enrichment by domain (1 credit). Returns the organization, or None if no match."""
    data = _request("GET", "/organizations/enrich", params={"domain": domain})
    return data.get("organization") or None


def search_people(domain: str, titles: list[str], page: int = 1, per_page: int = 100,
                  seniorities: list[str] | None = None) -> dict:
    """People API search at one firm (free). Names are obfuscated and there are no emails or phones.

    Returns Apollo's response: `people` (id, first_name, last_name_obfuscated, title, has_email, ...)
    and `total_entries`.
    """
    body = {"q_organization_domains_list": [domain], "page": page, "per_page": per_page}
    if titles:
        body["person_titles"] = list(titles)
    if seniorities:
        body["person_seniorities"] = list(seniorities)
    return _request("POST", "/mixed_people/api_search", json=body)


def reveal(ids: list[str]) -> list[dict]:
    """Bulk people match by Apollo person id (1 credit per match). Work emails only: no personal
    emails, no phones. Returns the matched people; ids Apollo cannot match are left out."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ApolloError("Choose at least one person to reveal.", 422)
    if len(ids) > MAX_REVEAL:
        raise ApolloError(f"Apollo reveals at most {MAX_REVEAL} people per request.", 422)
    data = _request("POST", "/people/bulk_match", json={
        "details": [{"id": person_id} for person_id in ids],
        "reveal_personal_emails": False, "reveal_phone_number": False})
    return [match for match in data.get("matches") or [] if match]


def search_orgs(filters: dict, page: int = 1) -> dict:
    """Organization search (1 credit per page). `filters` are Apollo's mixed_companies/search fields,
    e.g. q_organization_keyword_tags, organization_locations, organization_num_employees_ranges.

    Returns `organizations` (accounts merged in), `pagination` and `credits` spent (1).
    """
    body = {"per_page": 100, **filters, "page": page}
    data = _request("POST", "/mixed_companies/search", json=body)
    return {"organizations": (data.get("organizations") or []) + (data.get("accounts") or []),
            "pagination": data.get("pagination") or {}, "credits": 1}
