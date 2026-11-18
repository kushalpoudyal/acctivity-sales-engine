"""Apollo data as reviewable findings, next to the website brief.

Each finding has a stable `key` (approvals select by key), `source: "Apollo"`, the date Apollo
returned the data (`retrieved_at`) and no website evidence passage. Findings are rebuilt from
firms.apollo_org and the staff rows on every read, so they always match what is stored.
"""

APOLLO_SOURCE = "Source: Apollo"
TECH_NOTE = "Apollo's list of the firm's web and IT tools. Not verified; untick anything that looks wrong."


def _day(timestamp: str | None) -> str | None:
    return timestamp[:10] if timestamp else None


def _finding(key: str, category: str, title: str, text: str, retrieved_at: str | None, url: str = "") -> dict:
    label = f"{APOLLO_SOURCE}, retrieved {retrieved_at}" if retrieved_at else APOLLO_SOURCE
    return {"key": key, "category": category, "title": title, "text": text, "evidence": "",
            "source": "Apollo", "retrieved_at": retrieved_at,
            "sources": [{"url": url, "title": label}]}


def _names(values) -> list[str]:
    """Non-empty strings, de-duplicated ignoring case, in Apollo's order."""
    seen, result = set(), []
    for value in values or []:
        name = str(value).strip() if value is not None else ""
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def org_findings(org: dict | None, enriched_at: str | None) -> list[dict]:
    if not org:
        return []
    day = _day(enriched_at)
    categories = {str(t.get("name", "")).casefold(): t.get("category")
                  for t in org.get("current_technologies") or [] if isinstance(t, dict)}
    tech = _names(org.get("technology_names") or [t.get("name") for t in org.get("current_technologies") or []
                                                     if isinstance(t, dict)])
    industries = _names(org.get("industries") or [org.get("industry")])
    findings = [_finding(f"apollo:industry:{name.casefold()}", "Apollo: industries", name,
                         f"Apollo classifies the firm under the industry \"{name}\".", day)
                for name in industries]
    findings += [_finding(f"apollo:keyword:{name.casefold()}", "Apollo: keywords", name,
                          f"Apollo tags the firm with the keyword \"{name}\".", day)
                 for name in _names(org.get("keywords"))]
    findings += [_finding(f"apollo:tech:{name.casefold()}", "Apollo: tech stack", name,
                          f"Apollo lists {name}" + (f" ({categories[name.casefold()]})"
                                                    if categories.get(name.casefold()) else "")
                          + " among the firm's technologies.", day)
                 for name in tech]
    return findings


def contact_findings(staff: list[dict], enriched_at: str | None) -> list[dict]:
    """One finding per revealed Apollo contact (staff rows with an apollo_id)."""
    findings = []
    for person in staff:
        if not person.get("apollo_id"):
            continue
        email, linkedin = person.get("apollo_email"), person.get("linkedin_url")
        details = [f"work email {email}" + (f" ({person['email_status']})" if person.get("email_status") else "")
                   if email else "no work email found", f"LinkedIn {linkedin}" if linkedin else None]
        text = f"{person['full_name']}" + (f", {person['position']}" if person.get("position") else "") \
            + ": " + "; ".join(filter(None, details)) + "."
        finding = _finding(f"apollo:contact:{person['apollo_id']}", "Apollo: contacts", person["full_name"], text,
                           _day(person.get("apollo_revealed_at")) or _day(enriched_at), linkedin or "")
        finding["person"] = {"full_name": person["full_name"], "position": person.get("position"),
                             "apollo_email": email, "email_status": person.get("email_status"),
                             "linkedin_url": linkedin, "match_confidence": person.get("match_confidence")}
        findings.append(finding)
    return findings


def apollo_findings(org: dict | None, enriched_at: str | None, staff: list[dict]) -> list[dict]:
    return org_findings(org, enriched_at) + contact_findings(staff, enriched_at)
