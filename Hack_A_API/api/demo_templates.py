"""Hand-written data behind the demo plan: area-of-law templates, the seniority rate table and fictional names.

DRAFT, TO BE CHECKED BY SALES. Rates, fixed fees and disbursement amounts are plausible values drafted from
general knowledge of public Australian and New Zealand fee guides and court fee schedules. They are not quotes and
not anyone's real prices. All amounts exclude GST.

Matter types are named, not given ids: the plan resolves each name against the firm's Docs Core matter-type tree
(country and state), trying `aliases` in order. The first five templates cover the areas we demo most; a practice
area that matches none of them falls back to the closest template through FALLBACK_KEYWORDS, with a flag.
"""

DRAFT_NOTE = "Draft values, to be checked by sales. Amounts exclude GST."

SENIORITIES = ("managing_partner", "partner", "senior_associate", "associate", "paralegal")
SENIORITY_LABELS = {"managing_partner": "Managing partner", "partner": "Partner",
                    "senior_associate": "Senior associate", "associate": "Associate", "paralegal": "Paralegal"}

# Hourly rates (ex GST) by region, in seniority order above. Draft, to be checked by sales.
_RATES = {
    "AU-NSW": (650, 560, 460, 360, 190),
    "AU-VIC": (620, 540, 440, 350, 180),
    "AU-QLD": (570, 490, 410, 330, 170),
    "AU-WA": (570, 490, 410, 330, 170),
    "AU-SA": (520, 450, 380, 310, 160),
    "AU-ACT": (560, 480, 400, 320, 170),
    "AU-TAS": (480, 420, 350, 290, 150),
    "AU-NT": (520, 450, 380, 310, 160),
    "NZ": (550, 460, 380, 300, 160),
}
RATES = {region: dict(zip(SENIORITIES, values)) for region, values in _RATES.items()}
CURRENCIES = {"AU": "AUD", "NZ": "NZD"}
AU_STATES = ("NSW", "VIC", "QLD", "WA", "SA", "ACT", "TAS", "NT")


def mt(name, aliases, client, weight, descriptions, fixed_fee=None):
    """A matter type: names to resolve in Docs Core, the client kind, how often it appears and its fixed fee."""
    return {"name": name, "aliases": [name, *aliases], "client": client, "weight": weight,
            "descriptions": descriptions, "fixed_fee": fixed_fee}


def fee(description, amount, nz_amount=None):
    return {"description": description, "amounts": {"AU": amount, "NZ": nz_amount or amount}}


def disb(description, amount, country=None):
    """A disbursement; country limits it to AU or NZ matters."""
    return {"description": description, "amount": amount, "country": country}


TEMPLATES = {
    "family": {
        "label": "Family law",
        "keywords": ["family", "divorce", "separation", "parenting", "de facto", "relationship property",
                     "children", "custody"],
        "folders": ["Family Law", "Family"],
        "matter_types": [
            mt("Property Settlement", ["Relationship Property", "Property Division"], "person", 4,
               ["Property settlement after separation", "Division of relationship property"]),
            mt("Parenting", ["Care of Children", "Parenting Orders"], "person", 3,
               ["Parenting arrangements for two children", "Parenting orders application"]),
            mt("Divorce", ["Dissolution", "Divorce Application"], "person", 2,
               ["Divorce application", "Application to end the marriage"],
               fee("Divorce application (fixed fee)", 1450, 1200)),
            mt("Consent Orders", ["Consent Order"], "person", 2,
               ["Consent orders for property and parenting"],
               fee("Drafting consent orders (fixed fee)", 2750, 2400)),
            mt("Binding Financial Agreement", ["Financial Agreement", "Contracting Out Agreement"], "person", 1,
               ["Financial agreement before cohabitation"],
               fee("Financial agreement (fixed fee)", 3300, 2900)),
        ],
        "time_entries": [
            "Conference with client re separation and next steps",
            "Reviewing financial disclosure documents",
            "Drafting letter to the other party's solicitor",
            "Preparing affidavit material",
            "Telephone call with client re parenting arrangements",
            "Reviewing correspondence from the other party's solicitor",
            "Preparing for mediation",
            "Drafting proposed consent orders",
            "Preparing schedule of assets and liabilities",
        ],
        "disbursements": [
            disb("Court filing fee", 365, "AU"), disb("Family Court filing fee", 220, "NZ"),
            disb("Process server fee", 165), disb("Property valuation report", 880), disb("Title search", 30),
        ],
    },
    "conveyancing": {
        "label": "Conveyancing & property",
        "keywords": ["conveyanc", "real estate", "property", "residential", "land"],
        "folders": ["Conveyancing", "Conveyancing & Property", "Property"],
        "matter_types": [
            mt("Purchase", ["Property Purchase", "Residential Purchase"], "person", 4,
               ["Purchase of residential property", "Purchase of first home"],
               fee("Residential purchase (fixed fee)", 1650, 1500)),
            mt("Sale", ["Property Sale", "Residential Sale"], "person", 3,
               ["Sale of residential property", "Sale of investment unit"],
               fee("Residential sale (fixed fee)", 1450, 1350)),
            mt("Refinance", ["Mortgage Refinance"], "person", 1,
               ["Refinance of home loan"], fee("Refinance (fixed fee)", 990, 900)),
            mt("Transfer", ["Transfer of Land", "Property Transfer"], "person", 1,
               ["Transfer of property between spouses"], fee("Transfer of title (fixed fee)", 1100, 1000)),
        ],
        "time_entries": [
            "Reviewing contract for sale",
            "Ordering and reviewing searches",
            "Correspondence with lender re settlement",
            "Preparing transfer documents",
            "Calculating settlement adjustments",
            "Attending to settlement",
            "Telephone call with agent re special conditions",
            "Reporting to client on contract and searches",
        ],
        "disbursements": [
            disb("Title search", 30), disb("Council rates certificate", 180, "AU"),
            disb("Electronic settlement fee", 125, "AU"), disb("Land information memorandum", 350, "NZ"),
            disb("Registration fee", 90, "NZ"), disb("Courier", 25),
        ],
    },
    "wills_estates": {
        "label": "Wills & estates",
        "keywords": ["will", "estate", "probate", "succession", "attorney", "elder", "trust"],
        "folders": ["Wills & Estates", "Wills and Estates", "Estates", "Wills"],
        "matter_types": [
            mt("Will", ["Wills", "Will Preparation"], "person", 3,
               ["Will and powers of attorney", "Updated will after remarriage"],
               fee("Will and powers of attorney package (fixed fee)", 990, 900)),
            mt("Power of Attorney", ["Enduring Power of Attorney"], "person", 1,
               ["Enduring power of attorney"], fee("Power of attorney (fixed fee)", 450, 400)),
            mt("Probate", ["Grant of Probate"], "estate", 3,
               ["Application for grant of probate", "Probate and collection of assets"]),
            mt("Letters of Administration", ["Administration"], "estate", 1,
               ["Letters of administration, no will"]),
            mt("Estate Administration", ["Deceased Estate", "Estate"], "estate", 2,
               ["Administration of deceased estate", "Distribution of estate assets"]),
        ],
        "time_entries": [
            "Taking instructions for will",
            "Drafting will and powers of attorney",
            "Attending execution of will",
            "Preparing probate application",
            "Correspondence with executor re estate assets",
            "Writing to banks to collect estate funds",
            "Preparing estate accounts",
            "Telephone call with beneficiary re distribution",
        ],
        "disbursements": [
            disb("Probate filing fee", 865, "AU"), disb("High Court probate filing fee", 200, "NZ"),
            disb("Death certificate", 65), disb("Notice of intended distribution", 220, "AU"),
            disb("Gazette notice", 60, "NZ"),
        ],
    },
    "commercial": {
        "label": "Commercial",
        "keywords": ["commercial", "business", "corporate", "company", "contract", "franchis", "lease"],
        "folders": ["Commercial", "Business", "Corporate"],
        "matter_types": [
            mt("Business Sale", ["Sale of Business"], "company", 2, ["Sale of café business", "Sale of trade business"]),
            mt("Business Purchase", ["Purchase of Business"], "company", 2,
               ["Purchase of retail business", "Acquisition of dental practice"]),
            mt("Commercial Lease", ["Retail Lease", "Lease"], "company", 3,
               ["Review of retail shop lease", "New lease of warehouse premises"],
               fee("Commercial lease review (fixed fee)", 2200, 1950)),
            mt("Shareholders Agreement", ["Shareholders' Agreement"], "company", 1,
               ["Shareholders agreement for new venture"], fee("Shareholders agreement (fixed fee)", 3850, 3400)),
            mt("General Commercial", ["Commercial Advice", "Contract Review", "General"], "company", 2,
               ["Supply agreement review", "Terms of trade update"]),
        ],
        "time_entries": [
            "Reviewing draft agreement",
            "Drafting heads of agreement",
            "Conference with client re commercial terms",
            "Correspondence with other side re amendments",
            "Due diligence review of business records",
            "Preparing disclosure schedule",
            "Advising client on lease terms",
            "Finalising agreement for execution",
        ],
        "disbursements": [
            disb("ASIC company search", 25, "AU"), disb("Companies Office search", 10, "NZ"),
            disb("PPSR search", 4), disb("Courier", 25),
        ],
    },
    "litigation": {
        "label": "Litigation",
        "keywords": ["litigation", "dispute", "court", "debt", "recovery", "claim"],
        "folders": ["Litigation", "Disputes", "Dispute Resolution"],
        "matter_types": [
            mt("Debt Recovery", ["Debt Collection"], "company", 3,
               ["Recovery of unpaid invoices", "Debt recovery against former customer"],
               fee("Letter of demand (fixed fee)", 330, 300)),
            mt("Local Court", ["Magistrates Court", "Magistrates' Court"], "person", 1,
               ["Local Court claim for damages"]),
            mt("District Court", ["County Court"], "company", 2, ["Building defects claim", "Contract dispute"]),
            mt("Supreme Court", ["High Court"], "company", 1, ["Shareholder dispute proceedings"]),
            mt("General Litigation", ["Litigation", "Dispute Resolution"], "person", 2,
               ["Neighbour boundary dispute", "Insurance claim dispute"]),
        ],
        "time_entries": [
            "Reviewing client documents and chronology",
            "Drafting statement of claim",
            "Preparing letter of demand",
            "Correspondence with opposing solicitor",
            "Preparing for directions hearing",
            "Attending directions hearing",
            "Conference with counsel",
            "Preparing list of documents for discovery",
        ],
        "disbursements": [
            disb("Court filing fee", 290), disb("Process server fee", 165),
            disb("Barrister's fee for advice", 2750), disb("Transcript fee", 120),
        ],
    },
}

# Practice areas without a template of their own, mapped to the closest one (the plan flags these).
FALLBACK_KEYWORDS = [
    ("employment", "commercial"), ("workplace", "commercial"), ("industrial", "commercial"),
    ("tax", "commercial"), ("intellectual", "commercial"), ("banking", "commercial"), ("finance", "commercial"),
    ("immigration", "family"), ("domestic violence", "family"),
    ("criminal", "litigation"), ("traffic", "litigation"), ("injury", "litigation"),
    ("compensation", "litigation"), ("insurance", "litigation"), ("building", "litigation"),
    ("construction", "litigation"), ("planning", "conveyancing"), ("strata", "conveyancing"),
]
DEFAULT_FALLBACK = "commercial"

# Fictional client names. Combinations are generated, so any match with a real person is coincidence.
FIRST_NAMES = ["Mia", "Noah", "Ava", "Leo", "Isla", "Oliver", "Ruby", "Hugo", "Zara", "Felix", "Chloe", "Arlo",
               "Matilda", "Jasper", "Harper", "Theo", "Ivy", "Elijah", "Grace", "Lachlan", "Sienna", "Kai",
               "Willow", "Finn"]
LAST_NAMES = ["Thornton", "Abbott", "Kowalski", "Nguyen", "Fairweather", "Delaney", "Okafor", "Ramsay", "Whitlock",
              "Castellano", "Pemberton", "Hollis", "Tanaka", "Moreau", "Brennan", "Iyer", "Lindqvist", "Haddad",
              "Ashworth", "Petrakis", "Sinclair", "Mbeki", "Calloway", "Duarte"]
COMPANY_WORDS = ["Harbourline", "Bluegum", "Ironbark", "Summit Ridge", "Kestrel", "Saltbush", "Northgate",
                 "Copperleaf", "Riverbend", "Tidewater", "Granite Peak", "Wattle Grove", "Silverfern", "Redcliff"]
COMPANY_TRADES = ["Logistics", "Constructions", "Dental", "Coffee Roasters", "Plumbing", "Engineering",
                  "Landscaping", "Electrical", "Motors", "Cabinetry", "Physiotherapy", "Wholesale"]
COMPANY_SUFFIX = {"AU": "Pty Ltd", "NZ": "Limited"}
