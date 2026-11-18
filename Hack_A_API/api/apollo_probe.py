"""One-off Apollo API probe: plan access, NZ/AU law-firm coverage, tech stack, seniorities.

Spends at most ~7 credits: 1 firm-search page, 1 enrich per firm, 1 email reveal per firm
(people search is free). Prints a summary only: no names, emails or other personal data.

    docker compose exec api python apollo_probe.py                  # firms from Apollo search
    docker compose exec api python apollo_probe.py firm-a.co.nz ... # or name the domains
"""

import os
import sys
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE = "https://api.apollo.io/api/v1"
FIRMS = 3
TITLES = ["Managing Partner", "Partner", "Practice Manager", "General Manager",
          "Operations Manager", "Finance Manager", "CFO"]
SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager"]
LEGAL_TECH_HINTS = ["leap", "actionstep", "clio", "smokeball", "linetime", "lexis", "affinity",
                    "practice", "xero", "myob", "quickbooks", "legal", "law", "iManage", "netdocuments"]

credits = 0


def call(client, method, path, cost=0, **kwargs):
    global credits
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        print(f"  ! {method} {path} -> HTTP {response.status_code}: {response.text[:300]}")
        return None
    credits += cost
    return response.json()


def search_firms(client):
    print("\n== 1. Firm search (mixed_companies/search, 1 credit) ==")
    data = call(client, "POST", "/mixed_companies/search", cost=1, json={
        "q_organization_keyword_tags": ["law firm", "legal", "lawyers"],
        "organization_locations": ["new zealand", "australia"],
        "organization_num_employees_ranges": ["5,200"],
        "page": 1, "per_page": 25})
    if data is None:
        print("  Firm search blocked or failed: build without 'Find firms' (decision Q18).")
        return []
    orgs = (data.get("organizations") or []) + (data.get("accounts") or [])
    pagination = data.get("pagination", {})
    print(f"  total_entries={pagination.get('total_entries')} returned={len(orgs)}")
    print(f"  fields on a result: {sorted(orgs[0])[:40] if orgs else '-'}")
    domains = list(dict.fromkeys(o.get("primary_domain") for o in orgs if o.get("primary_domain")))
    with_naics = sum(1 for o in orgs if "5411" in " ".join(o.get("naics_codes") or []))
    print(f"  with domain={len(domains)} with NAICS 5411*={with_naics}")
    for o in orgs[:10]:
        print(f"  - {o.get('name')} | {o.get('primary_domain')} | {o.get('estimated_num_employees')}")
    return domains[:FIRMS]


def enrich(client, domain):
    data = call(client, "GET", "/organizations/enrich", cost=1, params={"domain": domain})
    org = (data or {}).get("organization") or {}
    if not org:
        print("  no organization match")
        return
    tech = org.get("technology_names") or []
    legal = [t for t in tech if any(h.lower() in t.lower() for h in LEGAL_TECH_HINTS)]
    print(f"  name={org.get('name')} employees={org.get('estimated_num_employees')} "
          f"country={org.get('country')} industry={org.get('industry')}")
    print(f"  industries={org.get('industries')} industry_tag_id={org.get('industry_tag_id')}")
    print(f"  keywords={(org.get('keywords') or [])[:12]}")
    print(f"  technologies ({len(tech)}): {tech[:25]}")
    print(f"  legal/accounting-looking tech: {legal or 'none'}")


def people(client, domain):
    base = {"q_organization_domains_list": [domain], "page": 1, "per_page": 100}
    by_title = call(client, "POST", "/mixed_people/api_search", json={**base, "person_titles": TITLES})
    found = (by_title or {}).get("people") or []
    print(f"  title search: total={(by_title or {}).get('total_entries')} returned={len(found)} "
          f"has_email={sum(1 for p in found if p.get('has_email'))} "
          f"has_direct_phone={sum(1 for p in found if p.get('has_direct_phone'))}")
    print(f"  titles: {Counter(p.get('title') for p in found).most_common(8)}")
    if found:
        print(f"  fields on a result: {sorted(found[0])}")
    for seniority in SENIORITIES:
        r = call(client, "POST", "/mixed_people/api_search", json={**base, "person_seniorities": [seniority],
                                                                   "per_page": 1})
        if r is not None:
            print(f"  seniority {seniority!r}: total={r.get('total_entries')}")
    return next((p for p in found if p.get("has_email")), None)


def reveal(client, person):
    data = call(client, "POST", "/people/bulk_match", cost=1, json={
        "details": [{"id": person["id"]}], "reveal_personal_emails": False, "reveal_phone_number": False})
    match = ((data or {}).get("matches") or [None])[0]
    if not match:
        print(f"  no match (credits_consumed={(data or {}).get('credits_consumed')})")
        return
    print(f"  email present={bool(match.get('email'))} email_status={match.get('email_status')} "
          f"linkedin present={bool(match.get('linkedin_url'))} "
          f"match_confidence={match.get('match_confidence')} "
          f"credits_consumed={(data or {}).get('credits_consumed')}")


def main():
    # override: Docker's env_file sets an empty value at container start; re-read the file now.
    load_dotenv(Path(__file__).with_name(".env"), override=True)
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        sys.exit("APOLLO_API_KEY is not set in Hack_A_API/api/.env.")
    with httpx.Client(base_url=BASE, timeout=30, headers={
            "x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"}) as client:
        domains = sys.argv[1:FIRMS + 1] or search_firms(client)
        if not domains:
            sys.exit("No domains to probe. Pass 2-3 firm domains as arguments.")
        for domain in domains:
            print(f"\n== {domain} ==")
            print(" 2. Enrich (1 credit)")
            enrich(client, domain)
            print(" 3. People search (free)")
            candidate = people(client, domain)
            print(" 4. Reveal one email (1 credit)")
            if candidate:
                reveal(client, candidate)
            else:
                print("  no candidate with has_email: skipped, no credit spent")
    print(f"\nCredits spent (estimated): {credits}")


if __name__ == "__main__":
    main()
