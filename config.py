"""
config.py — centralizovana konfiguracija
"""

from pathlib import Path

# ── Putanje ──
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = BASE_DIR / "exports"
DB_PATH = DATA_DIR / "leads.db"

# ── Pretrage (fallback ako se ne prosledi CLI) ──
DEFAULT_SEARCHES = [
    "teretana Subotica",
]
DEFAULT_MAX_RESULTS = 30

# ── Playwright ──
PLAYWRIGHT_HEADLESS = False
PLAYWRIGHT_LOCALE = "sr-RS"
PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ── Platforme koje smatramo zastarelim ──
OUTDATED_PLATFORMS = [
    "wix.com",
    "squarespace.com",
    "weebly.com",
    "jimdo.com",
    "webnode",
    "blogger.com",
    "wordpress.com",
    "yolasite.com",
    "site123.com",
    "strikingly.com",
]

# ── CSV / HubSpot kolone — stabilni nazivi ──
# NAPOMENA: "Scraped At" je zadržan za backward compat (alias za Last Scraped At).
# Novi korisnici treba da koriste "First Scraped At" / "Last Scraped At".
# Stare opportunity kolone ("Website Score") su aliasi za nove ("Website Opportunity Score").
CSV_COLUMNS = [
    "Company Name",
    "Category",
    "City",
    "Address",
    "Phone",
    "Website",
    "Google Maps URL",
    "Rating",
    "Review Count",
    "Instagram",
    "Facebook",
    "First Scraped At",
    "Last Scraped At",
    "Scraped At",  # deprecated alias
    "Source Query",
    "Source City",
    "Lead Status",
    "Lead Score",
    "Website Opportunity Score",
    "Website Score",  # deprecated alias
    "SEO Opportunity Score",
    "SEO Score",  # deprecated alias
    "Conversion Opportunity Score",
    "Conversion Score",  # deprecated alias
    "Automated Audit Status",
    "Audit Status",
    "Notes",
]

# Mapiranje HubSpot Company properties (standardna + custom)
# Standardna HubSpot Company polja: name, city, address, phone, website, etc.
# Custom polja moras rucno kreirati u HubSpot-u (Settings → Properties → Company)
# NAPOMENA: za opportunity skorove, koristi nove nazive (website_opportunity_score),
# stari (website_score) su aliasi.
HUBSPOT_COMPANY_PROPERTY_MAP = {
    "Company Name": "name",                          # HubSpot standard
    "Category": "industry",                          # ili custom: category
    "City": "city",                                  # HubSpot standard
    "Address": "address",                            # HubSpot standard
    "Phone": "phone",                                # HubSpot standard
    "Website": "website",                            # HubSpot standard → domain
    "Google Maps URL": "google_maps_url",            # CUSTOM
    "Rating": "google_rating",                       # CUSTOM (Number)
    "Review Count": "google_review_count",           # CUSTOM (Number)
    "Instagram": "instagram_url",                    # CUSTOM
    "Facebook": "facebook_url",                      # CUSTOM
    "First Scraped At": "first_scraped_at",          # CUSTOM (Date)
    "Last Scraped At": "last_scraped_at",            # CUSTOM (Date)
    "Scraped At": "scraped_at",                      # CUSTOM (Date) - alias
    "Source Query": "source_query",                  # CUSTOM (Text)
    "Source City": "source_city",                    # CUSTOM (Text)
    "Lead Status": "lead_status_zeltro",             # CUSTOM (Dropdown)
    "Lead Score": "lead_score_zeltro",               # CUSTOM (Number)
    "Website Opportunity Score": "website_opportunity_score",  # CUSTOM (Number)
    "Website Score": "website_score",                # CUSTOM alias
    "SEO Opportunity Score": "seo_opportunity_score",          # CUSTOM
    "SEO Score": "seo_score",                        # alias
    "Conversion Opportunity Score": "conversion_opportunity_score",  # CUSTOM
    "Conversion Score": "conversion_score",          # alias
    "Automated Audit Status": "automated_audit_status",  # CUSTOM (Dropdown)
    "Audit Status": "audit_status",                  # CUSTOM manual (Dropdown)
    "Notes": "notes_zeltro",                         # ili hs_notes_next_activity
}

# ── Scoring konstante ──
SCORING_NOT_EVALUATED = ""  # ili "Not Evaluated" — ostavljamo prazno za HubSpot number polja
