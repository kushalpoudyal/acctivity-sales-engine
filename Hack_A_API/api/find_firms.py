"""Find firms (#6): Apollo organization search into a review list that is never stored.

One POST = one page = 1 Apollo credit. Ticked results are added through the normal intake
(`POST /api/v1/firms`, status `new`); research is never started from here.

Firm-search results carry no employee count, industry or location (probe 18/11, context/APOLLO.md),
and legal recruiters match the same keywords and NAICS code. So each result is flagged instead of
silently trusted: `likely_recruiter` from its name/domain, and `naics_match` against the form's codes
(Apollo's search has no NAICS filter, so NAICS is checked on the returned `naics_codes`).
"""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import apollo
from prospects import database, firm_domain

router = APIRouter(prefix="/api/v1/apollo/firm-search", tags=["Apollo"])

DEFAULTS = {"keywords": ["law firm", "legal", "lawyers"], "naics": ["5411"],
            "locations": ["New Zealand", "Australia"], "employees": ["5,200"]}
MISSING_FIELDS = ["employee count", "industry", "location"]
RECRUITER = re.compile(r"recruit|staffing|talent|headhunt|personnel", re.IGNORECASE)


def _clean(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


class FirmSearch(BaseModel):
    keywords: list[str] = Field(default_factory=lambda: list(DEFAULTS["keywords"]), max_length=20)
    naics: list[str] = Field(default_factory=lambda: list(DEFAULTS["naics"]), max_length=20)
    locations: list[str] = Field(default_factory=lambda: list(DEFAULTS["locations"]), max_length=20)
    employees: list[str] = Field(default_factory=lambda: list(DEFAULTS["employees"]), max_length=10)
    page: int = Field(default=1, ge=1, le=500)

    @field_validator("keywords", "naics", "locations", "employees")
    @classmethod
    def strip_blanks(cls, values: list[str]) -> list[str]:
        return _clean(values)

    @field_validator("keywords")
    @classmethod
    def needs_keyword(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("Enter at least one keyword tag, so a credit is not spent on an unfiltered search.")
        return values

    @field_validator("naics")
    @classmethod
    def digits_only(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"\d{2,6}", v) for v in values):
            raise ValueError("NAICS codes are 2 to 6 digits, e.g. 5411.")
        return values

    @field_validator("employees")
    @classmethod
    def ranges(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"\d+,\d*", v) for v in values):
            raise ValueError('Employee ranges look like "5,200" (min,max) or "500," (no max).')
        return values


def _domain(org: dict) -> str | None:
    for raw in (org.get("primary_domain"), org.get("website_url")):
        if raw:
            try:
                return firm_domain(raw)
            except HTTPException:
                continue
    return None


def _phone(org: dict) -> str | None:
    primary = org.get("primary_phone")
    return org.get("phone") or (primary.get("number") if isinstance(primary, dict) else primary) or None


def _naics_match(codes: list, wanted: list[str]) -> bool | None:
    """True/False when both sides have codes; None when there is nothing to compare."""
    codes = [str(c) for c in codes or []]
    if not wanted or not codes:
        return None
    return any(code.startswith(prefix) for code in codes for prefix in wanted)


def review_rows(orgs: list[dict], naics: list[str]) -> list[dict]:
    """Shape one page of Apollo results for review: dedupe by domain, flag doubtful rows."""
    with database() as db:
        known = {row["domain"] for row in db.execute("SELECT domain FROM firms")}
    rows, seen = [], set()
    for org in orgs:
        domain = _domain(org)
        key = domain or f"apollo:{org.get('id')}"
        if key in seen:
            continue
        seen.add(key)
        name = org.get("name") or domain or "Unnamed organization"
        rows.append({
            "apollo_id": org.get("id"), "name": name, "domain": domain,
            "website_url": org.get("website_url"), "linkedin_url": org.get("linkedin_url"),
            "phone": _phone(org),
            "naics_codes": org.get("naics_codes") or [], "sic_codes": org.get("sic_codes") or [],
            "founded_year": org.get("founded_year"),
            "revenue": org.get("organization_revenue_printed"),
            "already_added": domain in known,
            "likely_recruiter": bool(RECRUITER.search(f"{name} {domain or ''}")),
            "naics_match": _naics_match(org.get("naics_codes"), naics),
        })
    return rows


@router.get("")
def search_defaults():
    """Form defaults and whether Apollo is configured. Free: no Apollo call."""
    return {"configured": apollo.configured(), "defaults": DEFAULTS, "cost_per_page": 1,
            "missing_fields": MISSING_FIELDS}


@router.post("")
def search_firms(body: FirmSearch | None = None):
    """One page of Apollo organization search: spends 1 credit. Nothing is stored."""
    body = body or FirmSearch()
    if not apollo.configured():
        raise HTTPException(503, apollo.NOT_CONFIGURED)
    filters = {"q_organization_keyword_tags": body.keywords}
    if body.locations:
        filters["organization_locations"] = body.locations
    if body.employees:
        filters["organization_num_employees_ranges"] = body.employees
    try:
        result = apollo.search_orgs(filters, page=body.page)
    except apollo.ApolloError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    pagination = result["pagination"]
    return {"page": body.page, "total_entries": pagination.get("total_entries"),
            "total_pages": pagination.get("total_pages"), "credits": result["credits"],
            "missing_fields": MISSING_FIELDS,
            "results": review_rows(result["organizations"], body.naics)}
