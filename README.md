# Zeltro Lead Scraper — Google Maps → SQLite → HubSpot

Personal lead-generation tool that scrapes local businesses from Google Maps, cleans and deduplicates them, stores them in SQLite (source of truth), runs a lightweight website audit + opportunity scoring, **prioritizes leads by business strength + opportunity**, and exports a HubSpot-ready CSV.

**Workflow**
```
Google Maps
  → scraper.py (Playwright)
  → validators.py
  → deduplication.py (5 levels)
  → database.py / SQLite (incremental, no CSV as DB)
  → website_audit.py (optional)
  → scoring.py (opportunity scores)
  → prioritization.py (business strength, priority, services, reason)
  → exporter.py → exports/hubspot_companies.csv
  → HubSpot Company → Contact → Deal (manual)
```

No Docker, no Postgres, no queues. Just Python + SQLite. Rule-based, predictable, explainable.

---

## Features

- Google Maps scraping via Playwright (rating, reviews, website, phone, address, place_id, Instagram/Facebook links)
- **SQLite is primary storage** — CSV is export-only
- **Incremental scraping** — re-running adds only new leads, updates mutable fields, never overwrites sales data (`lead_status`, `audit_status`, `notes`)
- **5-level deduplication** (place_id → normalized Maps URL → phone+address check → name+address → fuzzy name+city/address) — multiple locations stay separate
- **First / last scraped timestamps** + `source_query`/`source_city` + `scrape_runs` table
- **Website audit** (lightweight HTTP/HTML — no Lighthouse) with timeout/headers, non-blocking
- **Opportunity scoring** — `website/seo/conversion_opportunity_score` (0 = no issue, 10 = big opportunity), `lead_score` weighted; `None` when not enough data
- **Lead Prioritization Engine** — `business_strength_score 0-10`, `priority_score 0-100`, `priority HIGH/MEDIUM/LOW`, `lead_type`, `recommended_services`, `lead_reason`, `sales_angle`, `prioritization_confidence`
- **HubSpot-compatible CSV** — stable columns, UTF-8 with BOM, proper escaping for `čćšžđ`, `,` in addresses, `"` in names, sorted by `priority_score`
- Clear summary logging and CLI

---

## Project Structure

```
google-maps-scraper/
├── config.py          # paths, outdated platforms, CSV_COLUMNS, HubSpot map, BUSINESS_STRENGTH/PRIORITY/SERVICE configs
├── database.py        # SQLite schema, migrations, upsert, scrape_runs
├── validators.py      # normalize_* + is_valid_*
├── deduplication.py   # extract_place_id, find_existing, deduplicate_batch
├── scoring.py         # calc_*_opportunity_score, calc_lead_score, score_lead
├── scraper.py         # scrape_google_maps (Playwright) — strict rating 0-5, social vs website filter
├── website_audit.py   # audit_website, audit_leads_batch
├── prioritization.py  # Lead Prioritization Engine
├── exporter.py        # export_companies_csv (HubSpot)
├── main.py            # CLI orchestrator
├── main_legacy.py     # original monolith (backup)
├── test_suite.py      # dedup / incremental / scoring / audit / prioritization / CSV tests
├── data/
│   └── leads.db       # SQLite (gitignored, keep .gitkeep)
└── exports/
    └── hubspot_companies.csv  # gitignored
```

---

## Installation

**Requirements:** Python 3.10+ (tested on 3.14), Playwright.

```bash
git clone <your-repo>
cd google-maps-scraper

# create venv (recommended)
py -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install pandas playwright requests beautifulsoup4

# install browser for Playwright (Chromium)
playwright install chromium
```

`requests` and `beautifulsoup4` are optional — audit falls back to `urllib` + regex. `pandas` only for legacy script.

---

## Configuration

All defaults live in `config.py`:

```python
DEFAULT_SEARCHES = ["teretana Subotica"]
DEFAULT_MAX_RESULTS = 30
PLAYWRIGHT_HEADLESS = False
OUTDATED_PLATFORMS = ["wix.com", "squarespace.com", ...]
CSV_COLUMNS = [...]  # stable, do not rename without HubSpot remap

BUSINESS_STRENGTH_CONFIG = {
    "rating_scores": [(4.5,4),(4.0,2),(3.5,1)],
    "review_scores": [(9,0),(49,2),(99,4),(299,6),(599,8),(inf,10)],
    "weights": {"rating":0.6,"reviews":0.35,"social":0.05}
}
PRIORITY_CONFIG = {"weights":{"business":0.55,"opportunity":0.45}, "thresholds":{"HIGH":75,"MEDIUM":50}}
SERVICE_RECOMMENDATION_CONFIG = {"website_threshold":7,"seo_threshold":6,"conversion_threshold":6,"min_business_for_service":4}
```

Scoring weights and thresholds are centralized — change them without touching `prioritization.py` logic.

---

## Usage

### 1. Basic scraping — all gyms in Subotica

```bash
# single query
py main.py --query "teretana" --city "Subotica" --max-results 100 --headless

# all gyms — multiple queries cover all Google variants (deduplicated automatically)
py main.py --queries "teretana" "fitness" "gym" --city "Subotica" --max-results 100 --headless --prioritize-existing --show-top 20

# headless off for debugging
py main.py --query "teretana" --city "Subotica" --max-results 30
```

Each query is a separate Google Maps search (`teretana Subotica` → 35-120 results). Use `100` to get everything; re-running is safe — incremental dedup adds only new.

### 2. Export / migration

```bash
py main.py --export-only                          # export current DB
py main.py --migrate-csv --export-only            # import old zeltro_leads*.csv (safe to re-run)
py main.py --export-only --show-top 20            # export + show ranked
```

### 3. Website audit (without re-scraping Maps)

```bash
py main.py --audit-websites                       # max 50 per run
py main.py --audit-websites --audit-limit 20
py main.py --export-only --audit-websites
py main.py --query "teretana" --city "Subotica" --audit-websites --audit-limit 50
```

### 4. Prioritization (without re-scraping)

```bash
py main.py --prioritize-existing                  # recalc business/priority for all 29 leads
py main.py --prioritize-existing --export-only
py main.py --show-top 20                          # show TOP 20 by priority_score
py main.py --prioritize-existing --show-top 20    # recalc + show
```

### 5. Custom DB

```bash
py main.py --query "teretana" --city "Subotica" --db "data/my.db"
```

---

## Output

```
=======================================================
  ZELTRO LEAD SCRAPER - Google Maps -> SQLite -> HubSpot
=======================================================
  Raw listings found:       10
  Batch duplicates removed: 1
  Unique listings:          9
  New leads:                0
  Existing leads updated:   9
  Invalid records:          0
  Websites audited:         N/A (use --audit-websites)

  Export:   exports/hubspot_companies.csv
  Database: data/leads.db (29 ukupno)
  Scrape runs: 3
======================================================================
  TOP 20 LEADS — sortirano po priority_score
======================================================================
1. Fitness Body Concept
   Priority: 84 HIGH | Business Strength: 7
   Website: None | W:10 SEO:10 Conv:10 | Rating: 5.0 Reviews: 44
   Services: Website Development, Local SEO, Conversion Optimization
   Reason: Strong Google presence (5.0 rating, 44 reviews) but no official website
   Confidence: High
```

- **Database:** `data/leads.db` (incremental)
- **Export:** `exports/hubspot_companies.csv` (UTF-8 with BOM, sorted by `priority_score`)

---

## CSV / HubSpot

**Stable columns** (`config.py:CSV_COLUMNS`):

```
Company Name, Category, City, Address, Phone, Website, Google Maps URL,
Rating, Review Count, Instagram, Facebook,
First Scraped At, Last Scraped At, Scraped At (alias),
Source Query, Source City,
Lead Status, Lead Score,
Website Opportunity Score, Website Score (alias),
SEO Opportunity Score, SEO Score (alias),
Conversion Opportunity Score, Conversion Score (alias),
Automated Audit Status, Audit Status,
Business Strength Score, Priority Score, Priority, Lead Type,
Recommended Services, Lead Reason, Sales Angle, Prioritization Confidence,
Notes
```

- `Website/SEO/Conversion Score` are aliases for `* Opportunity Score`.
- `Scraped At` mirrors `Last Scraped At`.
- `Recommended Services` is `", "` joined JSON array.

**Create custom Company properties before import** (`Settings → Properties → Company`):

| CSV column | HubSpot property | Type |
|---|---|---|
| `Google Maps URL` | `google_maps_url` | Text |
| `Rating` | `google_rating` | Number |
| `Review Count` | `google_review_count` | Number |
| `Instagram` | `instagram_url` | Text |
| `Facebook` | `facebook_url` | Text |
| `First/Last Scraped At` | `first_scraped_at` / `last_scraped_at` | Date |
| `Source Query/City` | `source_query` / `source_city` | Text |
| `Lead Status` | `lead_status_zeltro` | Dropdown |
| `Lead Score` | `lead_score_zeltro` | Number |
| `Website Opportunity Score` | `website_opportunity_score` | Number |
| `SEO Opportunity Score` | `seo_opportunity_score` | Number |
| `Conversion Opportunity Score` | `conversion_opportunity_score` | Number |
| `Automated Audit Status` | `automated_audit_status` | Dropdown |
| `Audit Status` | `audit_status` | Dropdown |
| `Business Strength Score` | `business_strength_score` | Number 0-10 |
| `Priority Score` | `priority_score_zeltro` | Number 0-100 |
| `Priority` | `priority_zeltro` | Dropdown HIGH/MEDIUM/LOW |
| `Lead Type` | `lead_type` | Text |
| `Recommended Services` | `recommended_services` | Text |
| `Lead Reason` | `lead_reason` | Text |
| `Sales Angle` | `sales_angle` | Text |
| `Prioritization Confidence` | `prioritization_confidence` | Dropdown |

Standard fields map directly: `Company Name → name`, `City → city`, etc.

**HubSpot workflow:** `Company` (every scraped business) → `Contact` (when you find a person, associate) → `Deal` (only when qualified, e.g., `Power Gym — Website`). Never auto-create Deal per Company.

---

## Database & Incremental

- **First run:** `first_scraped_at = last_scraped_at = now()`
- **Re-scrape:** `first_scraped_at` frozen, `last_scraped_at` updated; scraped fields (`address`, `phone`, `website`, `rating`...) update; **protected** (`lead_status`, `audit_status`, `notes`) never change; **generated** (`business_strength_score`, `priority_score`...) can be recomputed via `--prioritize-existing`.
- `source_query/city` keep first query.
- `scrape_runs` logs `started_at/finished_at/query/number_found/new/updated/duplicates/invalid`.
- Migrations are automatic on `init_db()` — never loses data.

---

## Deduplication

Priority in `deduplication.py:find_existing`:
1. `place_id` (exact)
2. Normalized `Google Maps URL`
3. Normalized `phone` + address similarity `<0.70` → separate locations
4. Exact `name + address`
5. Fuzzy `name>0.85` + `city` or `address>0.80` (but `<0.70` address → not duplicate)

Multiple `NULL` `place_id` never collide.

---

## Scoring

`scoring.py` — opportunity model, never guesses:
- **Website:** `10` no website, `7` outdated, `2` good, `None` unknown
- **SEO:** needs audit; missing `title` +4, `meta` +3, `h1` +2, `viewport` +1 (cap 10)
- **Conversion:** missing `tel`/`form`/`booking`/`CTA` etc. sum to 10
- **Lead Score:** weighted `website×2 + seo×1 + conversion×1`
- `None` stays empty in CSV (`Not Evaluated`)

## Prioritization Engine

`prioritization.py` — filter, not decision-maker:
- **Business Strength 0-10:** `rating` (≥4.5→4, 4.0→2) scaled + `reviews` piecewise with diminishing returns + `social +1`, weights `0.6/0.35/0.05`. `None` if no data.
- **Priority 0-100:** `max_opportunity = max(website,seo,conversion)`, `priority = (business*0.55 + max_opp*0.45)*10`. Strong business + strong opportunity = HIGH; weak business + high opportunity = MEDIUM/LOW.
- **Services:** `opportunity>=7` (website) / `>=6` (seo/conv) + `business>=4` threshold, else no recommendation (no fabrication).
- **Lead Type:** `Website/Local SEO/Conversion/Performance/Multi-Service/Low Priority/Manual Review`
- **Reason/Sales Angle/Confidence:** short human-readable reason, conversation angle, `High/Medium/Low` (High needs `rating+reviews+website/audit`).

All thresholds in `config.py`.

---

## Website Audit

`website_audit.py:audit_website(url)` — `requests`/`urllib` + `BeautifulSoup`/regex fallback, 10s timeout, 500k cap, checks HTTPS/status/time/title/meta/h1/viewport/canonical/robots/sitemap/tel/mailto/form/booking/CTA/offer/maps/social. On failure `Unable to Audit`, never blocks run. `automated_audit_status` separate from manual `audit_status`.

---

## Validation

`validators.py:validate_lead` — `company_name` required, `phone` 8-15 digits, `URL` parse, `rating 0-5`, `review_count int≥0`, UTF-8/CSV escaping. One bad lead never crashes run.

---

## Testing

```bash
py test_suite.py  # 23 tests
```

Covers: place_id/URL/phone deduplication, multi-location, NULL place_id, protected fields, first/last, opportunity scoring, audit, CSV, rating/review validation (22117 bug), website social filter, prioritization (strong+no-website HIGH, weak+no-website LOW, SEO/Conversion, no-opportunity, missing data, social-only, no-duplicate).

---

## .gitignore

```gitignore
__pycache__/ / *.py[cod]
.venv/ venv/ / .env
data/* !data/.gitkeep / *.db
exports/* !exports/.gitkeep / *.csv
.vscode/ .idea/ / *.log / .DS_Store
```
Commit code only. Never commit `data/leads.db` or generated CSVs.

---

## Upload to GitHub

```bash
git add config.py database.py validators.py deduplication.py scoring.py scraper.py website_audit.py prioritization.py exporter.py main.py README.md .gitignore
git commit -m "feat(prioritization): add lead prioritization engine"
git push origin master
```

For history rewrite (removing `data`/`exports` from old commits):
```bash
git push --force-with-lease origin master
```

---

## Roadmap

- Direct HubSpot API exporter (`HubSpotExporter`)
- More business scoring rules from real sales data
- `requirements.txt` / `pyproject.toml`

## License

Personal tool — MIT recommended if open-sourcing.
