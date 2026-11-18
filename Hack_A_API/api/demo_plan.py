"""Demo plan: what the seeder writes into a rep's Acctivity test firm for one prospect (issue #18).

The plan is built deterministically from an approved research snapshot, a date ("today") and a seed:
templates per area of law set the structure, the rate table sets rates, and one optional OpenAI call
tailors client names, matter descriptions and time-entry notes only. Matter types are resolved by name
against the firm's Docs Core matter-type tree through an injected provider (see MatterTypes).
"""
import json
import os
import random
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from itertools import combinations

import openai
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import prospects

from demo_templates import (AU_STATES, COMPANY_SUFFIX, COMPANY_TRADES, COMPANY_WORDS, CURRENCIES, DEFAULT_FALLBACK,
                            DRAFT_NOTE, FALLBACK_KEYWORDS, FIRST_NAMES, LAST_NAMES, RATES, SENIORITIES,
                            SENIORITY_LABELS, TEMPLATES)

PLAN_VERSION = 1
MAX_STAFF = 6
# A matter-type provider returns the Docs Core matter types for a country and (AU) state as flat leaves:
# [{"id": "<guid>", "name": "Purchase", "path": ["Conveyancing & Property", "Purchase"]}, ...]
# where path is the folder chain from the tree's top level down to and including the leaf.
MatterTypes = Callable[[str, str | None], list[dict]]
# A tailor takes the tailoring request (see tailoring_request) and returns {"matters": [{key, client_name,
# description, time_entry_notes}]}. Anything else it returns is ignored.
Tailor = Callable[[dict], dict]
# Ranges the validator accepts (rep edits included).
RATE_RANGE = (50, 2000)
ENTRY_HOURS_RANGE = (0.1, 10)
DAY_HOURS_MAX = 12
FEE_RANGE = (50, 50000)
DISBURSEMENT_RANGE = (1, 20000)
MAX_MATTERS = 40
MAX_INVOICES = 6


class PlanError(ValueError):
    pass


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


# ---------------------------------------------------------------- region, areas and matter types

def infer_region(snapshot: dict, country: str | None = None, state: str | None = None) -> tuple[str, str | None, list]:
    """Country and AU state from the rep's choice, else the offices' addresses and the domain."""
    flags = []
    text = " ".join(f"{office.get('name') or ''} {office.get('address') or ''}" for office in snapshot.get("offices", []))
    domain = snapshot.get("domain", "")
    if not country:
        if domain.endswith(".nz") or re.search(r"\bnew zealand\b|\bNZ\b", text, re.I):
            country = "NZ"
        elif domain.endswith(".au") or re.search(rf"\b({'|'.join(AU_STATES)})\b", text) or re.search(r"australia", text, re.I):
            country = "AU"
        else:
            country = "AU"
            flags.append({"code": "region_assumed", "message": "Country not found in the research; assumed Australia."})
    if country == "NZ":
        return "NZ", None, flags
    if not state:
        names = {"new south wales": "NSW", "victoria": "VIC", "queensland": "QLD", "western australia": "WA",
                 "south australia": "SA", "australian capital territory": "ACT", "tasmania": "TAS",
                 "northern territory": "NT"}
        found = re.search(rf"\b({'|'.join(AU_STATES)})\b", text)
        state = found.group(1) if found else next((code for name, code in names.items() if name in text.lower()), None)
        if not state:
            state = "NSW"
            flags.append({"code": "region_assumed", "message": "State not found in the research; assumed NSW."})
    return "AU", state.upper(), flags


def region_key(country: str, state: str | None) -> str:
    return "NZ" if country == "NZ" else f"AU-{state}"


def area_template(name: str) -> tuple[str, bool]:
    """The template for a practice area name, and whether it is a fallback to the closest template."""
    text = _norm(name)
    for key, template in TEMPLATES.items():
        if any(word in text for word in template["keywords"]):
            return key, False
    return next((key for word, key in FALLBACK_KEYWORDS if word in text), DEFAULT_FALLBACK), True


def resolve_matter_type(name: str, leaves: list[dict], folder: str | None = None,
                        folders: list[str] | None = None) -> dict | None:
    """The Docs Core leaf with this name (ignoring case). A given folder must match the leaf's top folder;
    otherwise a leaf under one of the template's folders wins over one elsewhere in the tree."""
    matches = [leaf for leaf in leaves if _norm(leaf.get("name")) == _norm(name) and leaf.get("id")]
    if folder:
        matches = [leaf for leaf in matches if _norm((leaf.get("path") or [None])[0]) == _norm(folder)]
    preferred = [leaf for leaf in matches if folders and _norm((leaf.get("path") or [None])[0]) in map(_norm, folders)]
    return (preferred or matches or [None])[0]


def _template_types(key: str, leaves: list[dict]) -> list[dict]:
    """The template's matter types that resolve in this firm's tree, each with its Docs Core leaf."""
    template, resolved = TEMPLATES[key], []
    for matter_type in template["matter_types"]:
        for alias in matter_type["aliases"]:
            if leaf := resolve_matter_type(alias, leaves, folders=template["folders"]):
                resolved.append({**matter_type, "leaf": leaf})
                break
    return resolved


def practice_mix(snapshot: dict) -> tuple[list[dict], dict, list]:
    """Templates weighted by the firm's practice areas (1 + people linked to each area), heaviest first."""
    mix, by_area, flags = {}, {}, []
    people_per_area = {}
    for person in snapshot.get("people", []):
        for area in person.get("practice_areas") or []:
            people_per_area[_norm(area)] = people_per_area.get(_norm(area), 0) + 1
    for area in snapshot.get("practice_areas", []):
        key, fallback = area_template(area["name"])
        by_area[_norm(area["name"])] = key
        entry = mix.setdefault(key, {"template": key, "label": TEMPLATES[key]["label"], "weight": 0,
                                     "practice_areas": []})
        entry["weight"] += 1 + people_per_area.get(_norm(area["name"]), 0)
        entry["practice_areas"].append(area["name"])
        if fallback:
            flags.append({"code": "template_fallback", "practice_area": area["name"], "template": key,
                          "message": f"No template for \"{area['name']}\"; using {TEMPLATES[key]['label']}."})
    if not mix:
        mix[DEFAULT_FALLBACK] = {"template": DEFAULT_FALLBACK, "label": TEMPLATES[DEFAULT_FALLBACK]["label"],
                                 "weight": 1, "practice_areas": []}
        flags.append({"code": "no_practice_areas", "template": DEFAULT_FALLBACK,
                      "message": "No approved practice areas; using the commercial template."})
    order = list(TEMPLATES)
    return sorted(mix.values(), key=lambda m: (-m["weight"], order.index(m["template"]))), by_area, flags


# ---------------------------------------------------------------- staff

def seniority(title: str | None) -> str | None:
    """Seniority from a published title; None for people who are not fee earners (e.g. practice managers)."""
    text = _norm(title)
    if not text:
        return None
    if re.search(r"managing (partner|director|principal)|\bprincipal\b|\bfounder|\bowner\b", text):
        return "managing_partner"
    if re.search(r"\bsenior associate|special counsel|\bsenior (lawyer|solicitor)|\bconsultant\b|\bcounsel\b", text):
        return "senior_associate"
    if re.search(r"\bpartner\b|\bdirector\b", text):
        return "partner"
    if re.search(r"\bparalegal|law clerk|legal (assistant|secretary)|\bclerk\b|conveyancer", text):
        return "paralegal"
    if re.search(r"\bassociate\b|\bsolicitor\b|\blawyer\b|\bbarrister\b|\battorney\b", text):
        return "associate"
    return None


def _candidates(snapshot: dict, by_area: dict) -> list[dict]:
    """Website people, then approved Apollo contacts not already listed: names, titles and areas only."""
    people, seen = [], set()
    apollo = [{"full_name": item["person"]["full_name"], "position": item["person"].get("position"),
               "practice_areas": [], "source": "apollo"}
              for item in snapshot.get("apollo", []) if item.get("person")]
    for person in [*({**p, "source": "website"} for p in snapshot.get("people", [])), *apollo]:
        name = " ".join((person.get("full_name") or "").split())
        level = seniority(person.get("position"))
        if not name or _norm(name) in seen or not level:
            continue
        seen.add(_norm(name))
        areas = []
        for area in person.get("practice_areas") or []:
            key = by_area.get(_norm(area)) or area_template(area)[0]
            if key not in areas:
                areas.append(key)
        people.append({"full_name": name, "title": person["position"].strip(), "seniority": level,
                       "areas": areas, "source": person["source"]})
    return people


def select_staff(snapshot: dict, mix: list[dict], by_area: dict, region: str) -> tuple[list[dict], list]:
    """Up to 6 people: managing partner, 2 partners covering the main areas, senior associate, associate,
    paralegal; empty slots go to the next people by seniority."""
    people, chosen, flags = _candidates(snapshot, by_area), [], []

    def take(level, cover=None):
        for person in people:
            if person not in chosen and person["seniority"] == level and (cover is None or cover in person["areas"]):
                chosen.append(person)
                return person

    managing = take("managing_partner")
    partner_slots = 2 if managing else 3
    for entry in mix:
        if sum(p["seniority"] == "partner" for p in chosen) < partner_slots and not any(
                entry["template"] in p["areas"] for p in chosen if p["seniority"] == "partner"):
            take("partner", entry["template"])
    while sum(p["seniority"] == "partner" for p in chosen) < partner_slots and take("partner"):
        pass
    for level in ("senior_associate", "associate", "paralegal"):
        take(level)
    for level in SENIORITIES:
        while len(chosen) < MAX_STAFF and take(level):
            pass
    chosen.sort(key=lambda p: SENIORITIES.index(p["seniority"]))  # stable: keeps selection order within a level
    if not managing:
        flags.append({"code": "no_managing_partner", "message": "No managing partner or principal found."})
    if len(chosen) < MAX_STAFF:
        flags.append({"code": "staff_short", "message": f"Only {len(chosen)} fee earners found; "
                                                       f"{MAX_STAFF - len(chosen)} demo accounts stay unused."})
    if not chosen:
        raise PlanError("No fee earners with a recognisable title in the approved snapshot.")
    staff = []
    for slot, person in enumerate(chosen, 1):
        first, _, last = person["full_name"].partition(" ")
        staff.append({"slot": slot, "full_name": person["full_name"], "first_name": first, "last_name": last,
                      "title": person["title"], "seniority": person["seniority"],
                      "rate": RATES[region][person["seniority"]], "areas": person["areas"],
                      "source": person["source"]})
    return staff, flags


# ---------------------------------------------------------------- matters, time and billing

def _allocate(total: int, mix: list[dict]) -> list[int]:
    """Largest-remainder split of total matters by weight, at least one per template while possible."""
    weights = [m["weight"] for m in mix]
    shares = [total * w / sum(weights) for w in weights]
    counts = [max(1, int(s)) if i < total else 0 for i, s in enumerate(shares)]
    order = sorted(range(len(mix)), key=lambda i: (-(shares[i] - int(shares[i])), i))
    i = 0
    while sum(counts) < total:
        counts[order[i % len(order)]] += 1
        i += 1
    while sum(counts) > total:
        biggest = max(range(len(counts)), key=lambda j: (counts[j], -j))
        counts[biggest] -= 1
    return counts


def _client(rng: random.Random, kind: str, country: str, used: set, staff_names: set) -> dict:
    for _ in range(200):
        if kind == "company":
            name = f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_TRADES)} {COMPANY_SUFFIX[country]}"
            client = {"kind": "company", "name": name}
        else:
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            client = {"kind": kind, "first_name": first, "last_name": last,
                      "name": f"Estate of the late {first} {last}" if kind == "estate" else f"{first} {last}"}
        if _norm(client["name"]) not in used and _norm(f"{client.get('first_name')} {client.get('last_name')}") not in staff_names:
            used.add(_norm(client["name"]))
            return client
    raise PlanError("Ran out of fictional client names.")


def _working_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1) if (start + timedelta(days=n)).weekday() < 5]


def _split_hours(rng: random.Random, tenths: int) -> list[int]:
    """Split a day (in tenths of an hour) into 2-4 entries of at least 0.3 h."""
    parts = rng.randint(2, 4)
    cuts = sorted(rng.sample(range(3, tenths - 2), parts - 1)) if tenths > 6 * parts else []
    bounds = [0, *cuts, tenths]
    pieces = [b - a for a, b in zip(bounds, bounds[1:])]
    return pieces if all(p >= 3 for p in pieces) else [tenths]


def build_plan(snapshot: dict, today: date, seed: int, leaves: list[dict], country: str, state: str | None,
               flags: list) -> dict:
    rng = random.Random(seed)
    region = region_key(country, state)
    mix, by_area, mix_flags = practice_mix(snapshot)
    flags = [*flags, *mix_flags]
    types = {}
    for entry in list(mix):
        types[entry["template"]] = _template_types(entry["template"], leaves)
        if not types[entry["template"]]:
            mix.remove(entry)
            flags.append({"code": "template_unresolved", "template": entry["template"],
                          "message": f"None of the {entry['label']} matter types exist in Docs Core for {region}."})
    if not mix:
        raise PlanError(f"No template matter types resolve in the Docs Core tree for {region}.")
    staff, staff_flags = select_staff(snapshot, mix, by_area, region)
    flags += staff_flags
    staff_names = {_norm(s["full_name"]) for s in staff}

    weeks = rng.choice([3, 4])
    days = _working_days(today - timedelta(days=weeks * 7 - 1), today)
    start = days[0]
    counts = _allocate(rng.randint(15, 25), mix)
    for entry, count in zip(mix, counts):
        entry["matters"] = count

    # Matters: weighted matter type, fictional client, a responsible lawyer covering the area and a small team.
    leaders = [s for s in staff if s["seniority"] in ("managing_partner", "partner")] or staff[:1]
    juniors = [s for s in staff if s not in leaders]
    matters, used_clients = [], set()
    for entry, count in zip(mix, counts):
        template_types = types[entry["template"]]
        for _ in range(count):
            matter_type = rng.choices(template_types, weights=[t["weight"] for t in template_types])[0]
            covering = [s for s in leaders if entry["template"] in s["areas"]] or leaders
            responsible = rng.choice(covering)
            helpers = [s for s in juniors if entry["template"] in s["areas"]] + \
                      [s for s in juniors if entry["template"] not in s["areas"]]
            team = [responsible["slot"], *[s["slot"] for s in helpers[:rng.randint(1, 2)]]]
            client = _client(rng, matter_type["client"], country, used_clients, staff_names)
            surname = client.get("last_name") or client["name"].split()[0]
            matters.append({
                "key": f"M{len(matters) + 1:02d}", "template": entry["template"],
                "matter_type": {"name": matter_type["leaf"]["name"], "folder": (matter_type["leaf"].get("path") or [None])[0],
                                "id": matter_type["leaf"]["id"]},
                "description": f"{surname}: {rng.choice(matter_type['descriptions'])}",
                "client": client, "responsible": responsible["slot"], "team": sorted(set(team)),
                "opened_on": (start - timedelta(days=rng.randint(0, 120))).isoformat(),
                "fixed_fee": matter_type["fixed_fee"], "invoiced": False,
            })
    for member in staff:  # everyone needs at least one matter to record time against
        if not any(member["slot"] in m["team"] for m in matters):
            matter = rng.choice(matters)
            matter["team"] = sorted({*matter["team"], member["slot"]})

    # Time: every working day, 4-6 h per staff member, split across that person's matters.
    entries = []
    for member in staff:
        own = [m for m in matters if member["slot"] in m["team"]]
        for day in days:
            for tenths in _split_hours(rng, rng.randint(40, 60)):
                matter = rng.choice(own)
                hours = tenths / 10
                entries.append({"key": "", "matter": matter["key"], "staff": member["slot"], "date": day.isoformat(),
                                "hours": hours, "rate": member["rate"], "amount": round(hours * member["rate"], 2),
                                "description": rng.choice(TEMPLATES[matter["template"]]["time_entries"]),
                                "billed": False})
    entries.sort(key=lambda e: (e["date"], e["staff"], e["matter"]))
    for n, item in enumerate(entries, 1):
        item["key"] = f"T{n:03d}"

    # Fixed fees and disbursements.
    fees, disbursements = [], []
    for matter in matters:
        if matter["fixed_fee"]:
            fees.append({"key": f"F{len(fees) + 1:02d}", "matter": matter["key"], "staff": matter["responsible"],
                         "date": rng.choice(days).isoformat(), "description": matter["fixed_fee"]["description"],
                         "amount": matter["fixed_fee"]["amounts"][country], "billed": False})
        options = [d for d in TEMPLATES[matter["template"]]["disbursements"] if d["country"] in (None, country)]
        if options and rng.random() < 0.6:
            for item in rng.sample(options, min(len(options), rng.randint(1, 2))):
                disbursements.append({"key": f"D{len(disbursements) + 1:02d}", "matter": matter["key"],
                                      "date": rng.choice(days).isoformat(), "description": item["description"],
                                      "amount": item["amount"], "billed": False})
        del matter["fixed_fee"]

    # Invoices: each matter gets a candidate invoice date in the last fortnight; the 2-3 matters whose time up
    # to that date comes closest to 20% of all hours are invoiced, leaving about 80% unbilled.
    total = sum(e["hours"] for e in entries)
    issue_dates = {m["key"]: rng.choice(days[-9:-2] or days).isoformat() for m in matters}
    billable = {key: sum(e["hours"] for e in entries if e["matter"] == key and e["date"] <= issued)
                for key, issued in issue_dates.items()}
    keys = [m["key"] for m in matters if billable[m["key"]]]
    combos = [c for size in (2, 3) for c in combinations(keys, size)]
    best = min(combos, key=lambda c: (abs(sum(billable[k] for k in c) / total - 0.2), len(c), c)) if combos else keys
    invoices = []
    for matter in (m for m in matters if m["key"] in best):
        matter["invoiced"] = True
        issued = issue_dates[matter["key"]]
        items = {"time_entries": [e for e in entries if e["matter"] == matter["key"] and e["date"] <= issued],
                 "fees": [f for f in fees if f["matter"] == matter["key"] and f["date"] <= issued],
                 "disbursements": [d for d in disbursements if d["matter"] == matter["key"] and d["date"] <= issued]}
        for group in items.values():
            for item in group:
                item["billed"] = True
        invoices.append({"key": f"I{len(invoices) + 1:02d}", "matter": matter["key"], "date": issued,
                         **{name: [item["key"] for item in group] for name, group in items.items()}})

    return {
        "version": PLAN_VERSION, "today": today.isoformat(), "seed": seed,
        "firm": {"name": snapshot.get("firm"), "domain": snapshot.get("domain")},
        "region": {"country": country, "state": state, "key": region, "currency": CURRENCIES[country]},
        "rates_note": DRAFT_NOTE, "tailored": False, "flags": flags,
        "mix": [{key: m[key] for key in ("template", "label", "weight", "practice_areas", "matters")} for m in mix],
        "staff": staff, "matters": matters, "time_entries": entries, "fees": fees,
        "disbursements": disbursements, "invoices": invoices,
    }


# ---------------------------------------------------------------- tailoring

def tailoring_request(plan: dict) -> dict:
    """What the tailor sees: firm, region and each matter's area, type, client kind and current wording.
    No staff names or contact details."""
    return {"firm": plan["firm"]["name"], "region": plan["region"]["key"],
            "matters": [{"key": m["key"], "area": TEMPLATES[m["template"]]["label"],
                         "matter_type": m["matter_type"]["name"], "client_kind": m["client"]["kind"],
                         "client_name": m["client"]["name"], "description": m["description"]}
                        for m in plan["matters"]]}


def _clean(text, limit: int) -> str | None:
    text = " ".join(str(text or "").split())
    return text if 0 < len(text) <= limit and "@" not in text else None


def apply_tailoring(plan: dict, tailored: dict) -> dict:
    """Copy tailored client names, matter descriptions and time-entry notes into the plan. Everything else
    (keys, types, staff, dates, hours, amounts) stays as generated; unusable values are ignored."""
    matters = {m["key"]: m for m in plan["matters"]}
    staff_names = {_norm(s["full_name"]) for s in plan["staff"]}
    used = {_norm(m["client"]["name"]) for m in plan["matters"]}
    changed = False
    for item in (tailored or {}).get("matters") or []:
        if not isinstance(item, dict) or (matter := matters.get(item.get("key"))) is None:
            continue
        name = _clean(item.get("client_name"), 120)
        if name and _norm(name) not in staff_names and (_norm(name) == _norm(matter["client"]["name"]) or _norm(name) not in used):
            used.discard(_norm(matter["client"]["name"]))
            used.add(_norm(name))
            client = matter["client"]
            client["name"] = name
            if client["kind"] == "person":
                client["first_name"], _, client["last_name"] = name.partition(" ")
            changed = True
        if description := _clean(item.get("description"), 160):
            matter["description"] = description
            changed = True
        notes = [note for note in (_clean(n, 160) for n in (item.get("time_entry_notes") or [])[:8]) if note]
        if notes:
            for n, entry in enumerate(e for e in plan["time_entries"] if e["matter"] == matter["key"]):
                entry["description"] = notes[n % len(notes)]
            changed = True
    plan["tailored"] = changed
    return plan


class TailoredMatter(BaseModel):
    key: str = Field(description="The matter key from the request, unchanged.")
    client_name: str = Field(description="A fictional client name of the same kind (person, company or estate), "
                                         "under 80 characters. Never a real person or business.")
    description: str = Field(description="Matter description under 120 characters, in the style "
                                         "'<client surname or short name>: <what the matter is>'.")
    time_entry_notes: list[str] = Field(description="Three to six short time-entry notes (under 90 characters "
                                                    "each) a lawyer would write for this matter.")


class Tailoring(BaseModel):
    matters: list[TailoredMatter]


def tailor_with_openai(request: dict) -> dict:
    """One paid call: fictional client names, matter descriptions and time-entry notes that suit the firm."""
    with openai.OpenAI(timeout=120, max_retries=0) as client:
        response = client.responses.parse(
            model=os.getenv("OPENAI_DEMO_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-6.1-sol",
            text_format=Tailoring, max_output_tokens=12000,
            instructions=(
                "You write sample data for a private software demo for the law firm named in the request. For each "
                "matter, write a fictional client name of the given client_kind (for 'estate' use 'Estate of the "
                "late <name>'; companies use the region's company suffix), a short matter description and a few "
                "time-entry notes that fit the area of law, matter type and region. Every client must be fictional: "
                "never use real people, real businesses or the firm's own staff. No email addresses, phone numbers, "
                "amounts or dates. Plain, professional wording, no emoji. Return every matter key exactly once."),
            input=json.dumps(request),
        )
    if response.status != "completed" or response.output_parsed is None:
        raise PlanError("OpenAI did not return tailored demo data.")
    return response.output_parsed.model_dump()


# ---------------------------------------------------------------- public entry points

def generate_plan(snapshot: dict, *, today: date, seed: int, matter_types: MatterTypes,
                  tailor: Tailor | None = None, country: str | None = None, state: str | None = None) -> dict:
    """A complete, validated demo plan. Same snapshot, today, seed, matter types and tailor output → same plan."""
    country, state, flags = infer_region(snapshot, country, state)
    leaves = matter_types(country, state)
    plan = build_plan(snapshot, today, seed, leaves, country, state, flags)
    if tailor:
        try:
            plan = apply_tailoring(plan, tailor(tailoring_request(plan)))
        except Exception as exc:  # tailoring is optional: keep the template wording
            plan["flags"].append({"code": "tailoring_failed", "message": f"Tailoring failed ({type(exc).__name__}); "
                                                                         "template wording kept."})
    if errors := validate_plan(plan, matter_types=lambda c, s: leaves, today=today):
        raise PlanError(f"Generated plan is invalid: {errors[0]['message']}")
    return plan


def validate_plan(plan: dict, *, matter_types: MatterTypes, today: date) -> list[dict]:
    """Problems that would stop the seeder, as [{"path", "message"}]. Empty means valid."""
    try:
        return _validate(plan, matter_types, today)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return [{"path": "", "message": f"The plan is malformed ({type(exc).__name__}: {exc})."}]


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate(plan: dict, matter_types: MatterTypes, today: date) -> list[dict]:
    errors = []

    def error(path, message):
        errors.append({"path": path, "message": message})

    def check_date(path, value):
        day = _date(value)
        if day is None:
            error(path, f"{value!r} is not a date (YYYY-MM-DD).")
        elif day > today:
            error(path, f"{value} is after today ({today.isoformat()}).")
        return day

    if plan.get("version") != PLAN_VERSION:
        error("version", f"Expected plan version {PLAN_VERSION}.")
    region = plan["region"]
    country, state = region["country"], region.get("state")
    if country not in CURRENCIES or (country == "AU" and state not in AU_STATES):
        error("region", "Region must be NZ or an Australian state.")
        return errors
    leaves = {leaf["id"]: leaf for leaf in matter_types(country, state) if leaf.get("id")}

    staff = plan["staff"]
    if not 1 <= len(staff) <= MAX_STAFF:
        error("staff", f"Plan needs 1 to {MAX_STAFF} staff.")
    rates = {}
    for i, member in enumerate(staff):
        path = f"staff[{i}]"
        if member.get("slot") in rates or member.get("slot") not in range(1, MAX_STAFF + 1):
            error(f"{path}.slot", f"Slots are unique numbers from 1 to {MAX_STAFF}.")
        if not _norm(member.get("first_name")) or not _norm(member.get("title")):
            error(path, "Staff need a first name and a title.")
        if member.get("seniority") not in SENIORITIES:
            error(f"{path}.seniority", f"Seniority must be one of {', '.join(SENIORITIES)}.")
        if not (_number(member.get("rate")) and RATE_RANGE[0] <= member["rate"] <= RATE_RANGE[1]):
            error(f"{path}.rate", f"Hourly rate must be between {RATE_RANGE[0]} and {RATE_RANGE[1]}.")
        rates[member.get("slot")] = member.get("rate")
        if any(key in member for key in ("email", "phone", "public_work_email", "public_work_phone", "apollo_email")):
            error(path, "Staff carry names and titles only, no contact details.")

    matters = plan["matters"]
    if not 1 <= len(matters) <= MAX_MATTERS:
        error("matters", f"Plan needs 1 to {MAX_MATTERS} matters.")
    keys = {}
    for i, matter in enumerate(matters):
        path = f"matters[{i}]"
        if matter.get("key") in keys or not matter.get("key"):
            error(f"{path}.key", "Matter keys must be unique.")
        keys[matter.get("key")] = matter
        matter_type = matter.get("matter_type") or {}
        leaf = leaves.get(matter_type.get("id"))
        if not leaf or _norm(leaf.get("name")) != _norm(matter_type.get("name")):
            error(f"{path}.matter_type", f"\"{matter_type.get('name')}\" is not a Docs Core matter type for "
                                         f"{region_key(country, state)}.")
        if matter.get("template") not in TEMPLATES:
            error(f"{path}.template", "Unknown template.")
        client = matter.get("client") or {}
        if client.get("kind") not in ("person", "company", "estate") or not _norm(client.get("name")):
            error(f"{path}.client", "Each matter needs a fictional client with a name and kind.")
        if not _norm(matter.get("description")):
            error(f"{path}.description", "Each matter needs a description.")
        team = matter.get("team") or []
        if matter.get("responsible") not in rates or matter.get("responsible") not in team or not set(team) <= set(rates):
            error(f"{path}.team", "Responsible lawyer and team must be staff slots in the plan.")
        check_date(f"{path}.opened_on", matter.get("opened_on"))

    daily = {}
    for name, items in (("time_entries", plan["time_entries"]), ("fees", plan["fees"]),
                        ("disbursements", plan["disbursements"])):
        seen = set()
        for i, item in enumerate(items):
            path = f"{name}[{i}]"
            if item.get("key") in seen or not item.get("key"):
                error(f"{path}.key", "Keys must be unique.")
            seen.add(item.get("key"))
            matter = keys.get(item.get("matter"))
            if not matter:
                error(f"{path}.matter", "Refers to a matter that is not in the plan.")
            day = check_date(f"{path}.date", item.get("date"))
            if matter and day and (opened := _date(matter.get("opened_on"))) and day < opened:
                error(f"{path}.date", "Dated before the matter was opened.")
            if not _norm(item.get("description")):
                error(f"{path}.description", "A description is required.")
            if name == "disbursements":
                low, high = DISBURSEMENT_RANGE
            elif name == "fees":
                low, high = FEE_RANGE
            if name != "time_entries":
                if not (_number(item.get("amount")) and low <= item["amount"] <= high):
                    error(f"{path}.amount", f"Amount must be between {low} and {high}.")
                if name == "fees" and item.get("staff") not in rates:
                    error(f"{path}.staff", "Fees are billed to a staff slot in the plan.")
                continue
            hours = item.get("hours")
            if not (_number(hours) and ENTRY_HOURS_RANGE[0] <= hours <= ENTRY_HOURS_RANGE[1]
                    and abs(hours * 10 - round(hours * 10)) < 1e-6):
                error(f"{path}.hours", f"Hours must be {ENTRY_HOURS_RANGE[0]} to {ENTRY_HOURS_RANGE[1]} in 0.1 h steps.")
                continue
            if item.get("staff") not in rates:
                error(f"{path}.staff", "Time is recorded by a staff slot in the plan.")
                continue
            if matter and item["staff"] not in (matter.get("team") or []):
                error(f"{path}.staff", "Time is recorded by someone on the matter's team.")
            if item.get("rate") != rates[item["staff"]] or not _number(item.get("amount")) \
                    or abs(item["amount"] - round(hours * item["rate"], 2)) > 0.005:
                error(f"{path}.amount", "Rate must be the staff member's rate and amount hours × rate.")
            daily[(item["staff"], item.get("date"))] = daily.get((item["staff"], item.get("date")), 0) + hours
    for (slot, day), hours in sorted(daily.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        if hours > DAY_HOURS_MAX + 1e-6:
            error("time_entries", f"Staff slot {slot} records {hours:.1f} h on {day} (max {DAY_HOURS_MAX}).")

    invoices = plan["invoices"]
    if len(invoices) > MAX_INVOICES:
        error("invoices", f"At most {MAX_INVOICES} invoices.")
    billed = {name: {} for name in ("time_entries", "fees", "disbursements")}
    for i, invoice in enumerate(invoices):
        path = f"invoices[{i}]"
        if invoice.get("matter") not in keys:
            error(f"{path}.matter", "Refers to a matter that is not in the plan.")
        elif not keys[invoice["matter"]].get("invoiced"):
            error(f"{path}.matter", "The matter is not marked as invoiced.")
        issued = check_date(f"{path}.date", invoice.get("date"))
        for name in billed:
            items = {item.get("key"): item for item in plan[name]}
            for key in invoice.get(name) or []:
                item = items.get(key)
                if not item or item.get("matter") != invoice.get("matter"):
                    error(f"{path}.{name}", f"{key} is not an item on this invoice's matter.")
                elif key in billed[name]:
                    error(f"{path}.{name}", f"{key} is on more than one invoice.")
                elif issued and (item_day := _date(item.get("date"))) and item_day > issued:
                    error(f"{path}.{name}", f"{key} is dated after the invoice.")
                billed[name][key] = True
    for name in billed:
        for i, item in enumerate(plan[name]):
            if bool(item.get("billed")) != (item.get("key") in billed[name]):
                error(f"{name}[{i}].billed", "Billed must match whether an invoice includes the item.")
    for i, matter in enumerate(matters):
        if matter.get("invoiced") and not any(inv.get("matter") == matter.get("key") for inv in invoices):
            error(f"matters[{i}].invoiced", "Marked as invoiced but has no invoice.")
    return errors


def normalise_edit(edited: dict, stored: dict, leaves: list[dict]) -> dict:
    """A rep's edited plan with derived fields recomputed: matter-type ids re-resolved from the chosen names
    (and folder, if given), time-entry rates and amounts from the staff rates, billed and invoiced flags from
    the invoices. The plan's version, date, seed, firm and region stay as generated."""
    plan = {**edited, **{key: stored[key] for key in ("version", "today", "seed", "firm", "region", "rates_note")}}
    try:
        for matter in plan["matters"]:
            chosen = matter.get("matter_type") or {}
            leaf = resolve_matter_type(chosen.get("name") or "", leaves, folder=chosen.get("folder"),
                                       folders=TEMPLATES.get(matter.get("template"), {}).get("folders"))
            matter["matter_type"] = ({"name": leaf["name"], "folder": (leaf.get("path") or [None])[0], "id": leaf["id"]}
                                     if leaf else {"name": chosen.get("name"), "folder": chosen.get("folder"), "id": None})
        rates = {member["slot"]: member.get("rate") for member in plan["staff"]}
        for entry in plan["time_entries"]:
            if _number(rates.get(entry.get("staff"))) and _number(entry.get("hours")):
                entry["rate"] = rates[entry["staff"]]
                entry["amount"] = round(entry["hours"] * entry["rate"], 2)
        for name in ("time_entries", "fees", "disbursements"):
            on_invoice = {key for invoice in plan["invoices"] for key in invoice.get(name) or []}
            for item in plan[name]:
                item["billed"] = item.get("key") in on_invoice
        invoiced = {invoice.get("matter") for invoice in plan["invoices"]}
        for matter in plan["matters"]:
            matter["invoiced"] = matter.get("key") in invoiced
    except (KeyError, TypeError, AttributeError):
        pass  # validation reports the malformed part
    return plan


# ---------------------------------------------------------------- API

# Set by the rep-login work (#17/#19) to a MatterTypes function that reads Docs Core
# `GET v1/precedent-tree/matter-types` with the rep's demo-firm token and flattens it into leaves.
matter_type_provider: MatterTypes | None = None


class MatterTypesUnavailable(RuntimeError):
    """A provider raises this when it cannot read the tree (e.g. the rep's token has expired)."""


def set_matter_type_provider(provider: MatterTypes | None):
    global matter_type_provider
    matter_type_provider = provider


def current_date() -> date:
    """The local date; plans never extend past it."""
    return date.today()


router = APIRouter(prefix="/api/v1/firms", tags=["Demo plan"])


class PlanRequest(BaseModel):
    approval_id: int
    country: str | None = Field(default=None, pattern="^(AU|NZ)$")
    state: str | None = Field(default=None, pattern=f"^({'|'.join(AU_STATES)})$")
    seed: int | None = Field(default=None, ge=0, le=2**31)
    today: date | None = None
    tailor: bool = True


class PlanEdit(BaseModel):
    plan: dict


def _leaves(country: str, state: str | None) -> list[dict]:
    if matter_type_provider is None:
        raise HTTPException(503, "Docs Core matter types need the rep's Acctivity login, which is not set up yet.")
    try:
        return matter_type_provider(country, state)
    except MatterTypesUnavailable as exc:
        raise HTTPException(503, str(exc) or "Docs Core matter types are unavailable.") from exc


def _saved(row) -> dict:
    return {"firm_id": row["firm_id"], "approval_id": row["approval_id"], "plan": json.loads(row["plan"]),
            "errors": json.loads(row["errors"]), "edited": bool(row["edited"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


@router.post("/{firm_id}/demo-plan")
def create_plan(firm_id: int, body: PlanRequest):
    """Generate the demo plan from an approved snapshot (one paid OpenAI call unless tailor is false) and save it
    as the firm's plan, replacing any earlier one."""
    with prospects.database() as db:
        row = db.execute("SELECT snapshot FROM proposals WHERE id=? AND firm_id=?",
                         (body.approval_id, firm_id)).fetchone()
    if not row:
        raise HTTPException(404, "Approved snapshot not found for this firm.")
    today = body.today or current_date()
    if today > current_date():
        raise HTTPException(422, "The plan cannot end in the future.")
    snapshot = json.loads(row["snapshot"])
    country, state, _ = infer_region(snapshot, body.country, body.state)
    leaves = _leaves(country, state)
    try:
        plan = generate_plan(snapshot, today=today, seed=body.approval_id if body.seed is None else body.seed,
                             matter_types=lambda c, s: leaves, tailor=tailor_with_openai if body.tailor else None,
                             country=country, state=state)
    except PlanError as exc:
        raise HTTPException(422, str(exc)) from exc
    now = datetime.now(timezone.utc).isoformat()
    with prospects.database() as db:
        db.execute("""INSERT INTO demo_plans(firm_id,approval_id,plan,errors,edited,created_at,updated_at)
                      VALUES(?,?,?,'[]',0,?,?) ON CONFLICT(firm_id) DO UPDATE SET approval_id=excluded.approval_id,
                      plan=excluded.plan,errors='[]',edited=0,created_at=excluded.created_at,
                      updated_at=excluded.updated_at""", (firm_id, body.approval_id, json.dumps(plan), now, now))
    return get_plan(firm_id)


@router.get("/{firm_id}/demo-plan")
def get_plan(firm_id: int):
    with prospects.database() as db:
        row = db.execute("SELECT * FROM demo_plans WHERE firm_id=?", (firm_id,)).fetchone()
    if not row:
        raise HTTPException(404, "No demo plan for this firm yet.")
    return _saved(row)


@router.put("/{firm_id}/demo-plan")
def edit_plan(firm_id: int, body: PlanEdit):
    """Save the rep's edits to the whole plan. Derived fields are recomputed and the plan is re-validated;
    an invalid plan is refused (422, detail.errors) and the saved plan stays as it was."""
    stored = get_plan(firm_id)["plan"]
    region = stored["region"]
    leaves = _leaves(region["country"], region["state"])
    plan = normalise_edit(body.plan, stored, leaves)
    if errors := validate_plan(plan, matter_types=lambda c, s: leaves, today=current_date()):
        raise HTTPException(422, {"message": "The plan has problems; nothing was saved.", "errors": errors})
    with prospects.database() as db:
        db.execute("UPDATE demo_plans SET plan=?,errors='[]',edited=1,updated_at=? WHERE firm_id=?",
                   (json.dumps(plan), datetime.now(timezone.utc).isoformat(), firm_id))
    return get_plan(firm_id)
