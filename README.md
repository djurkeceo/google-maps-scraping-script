# Zeltro Lead Scraper — Google Maps → SQLite → HubSpot

Personal lead-generation tool that scrapes local businesses from Google Maps, cleans and deduplicates them, stores them in SQLite (source of truth), runs a lightweight website audit + opportunity scoring, and exports a HubSpot-ready CSV.

**Workflow**
```
Google Maps
  → scraper.py (Playwright)
  → validators.py
  → deduplication.py (5 levels)
  → database.py / SQLite (incremental, no CSV as DB)
  → website_audit.py (optional)
  → scoring.py
  → exporter.py → exports/hubspot_companies.csv
  → HubSpot Company → Contact → Deal (manual)
```

No Docker, no Postgres, no queues. Just Python + SQLite.

---

## Features

- Google Maps scraping via Playwright (rating, reviews, website, phone, address, place_id, Instagram/Facebook links)
- **SQLite is primary storage** — CSV is export-only
- **Incremental scraping** — re-running adds only new leads, updates mutable fields, never overwrites sales data
- **5-level deduplication** (place_id → normalized Maps URL → phone+address check → name+address → fuzzy name+city/address) — multiple locations of the same chain stay separate
- **Protected fields** — `lead_status`, `audit_status`, `notes` are never auto-overwritten
- **First / last scraped timestamps** + `source_query`/`source_city` tracking + `scrape_runs` table
- **Website audit** (lightweight HTTP/HTML — no Lighthouse) with timeout/headers/max-redirects, non-blocking
- **Opportunity scoring** — `website/seo/conversion_opportunity_score` (0 = no issue, 10 = big opportunity), `lead_score` is weighted; returns `None`/`""` when not enough data instead of guessing
- **HubSpot-compatible CSV** — stable columns, UTF-8 with BOM, proper escaping for `čćšžđ`, `,` in addresses, `"` in names
- Clear summary logging and CLI

---

## Project Structure

```
google-maps-scraper/
├── config.py          # paths, outdated platforms, CSV_COLUMNS, HubSpot property map
├── database.py        # SQLite schema, migrations, upsert, scrape_runs
├── validators.py      # normalize_* + is_valid_*
├── deduplication.py   # extract_place_id, find_existing, deduplicate_batch
├── scoring.py         # calc_*_opportunity_score, calc_lead_score, score_lead
├── scraper.py         # scrape_google_maps (Playwright)
├── website_audit.py   # audit_website, audit_leads_batch
├── exporter.py        # export_companies_csv (HubSpot)
├── main.py            # CLI orchestrator
├── main_legacy.py     # original monolith (backup)
├── test_suite.py      # deduplication / incremental / scoring / audit / CSV tests
├── data/
│   └── leads.db       # SQLite (gitignored)
└── exports/
    └── hubspot_companies.csv
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

`requests` and `beautifulsoup4` are optional — the audit falls back to `urllib` + regex if they’re missing. `pandas` is only needed for the legacy script; the current exporter uses `csv`.

---

## Configuration

All defaults live in `config.py`:

```python
# config.py
DEFAULT_SEARCHES = ["teretana Subotica"]  # fallback if no CLI query
DEFAULT_MAX_RESULTS = 30
PLAYWRIGHT_HEADLESS = False               # set True for headless
OUTDATED_PLATFORMS = ["wix.com", "squarespace.com", ...]
CSV_COLUMNS = [...]                       # stable, do not rename without HubSpot remap
```

Edit `OUTDATED_PLATFORMS` to change what counts as “outdated” website. Scoring weights live in `scoring.py`.

---

## Usage

### 1. Basic scraping

```bash
# single query + city
py main.py --query "teretana" --city "Subotica" --max-results 30

# multiple queries at once (separate Google Maps searches, merged & deduped)
py main.py --queries "teretana" "gym" "fitness" --city "Subotica" --max-results 100

# headless (no browser window)
py main.py --query "teretana" --city "Subotica" --headless
```

### 2. Export / migration without scraping

```bash
# export current DB to CSV
py main.py --export-only

# migrate old zeltro_leads*.csv files into SQLite (safe to run multiple times)
py main.py --migrate-csv --export-only
```

### 3. Website audit (without re-scraping Maps)

```bash
# audit existing leads that have a website (max 50 per run by default)
py main.py --audit-websites
py main.py --audit-websites --audit-limit 20

# audit + export in one run
py main.py --export-only --audit-websites

# audit during scraping (scores use fresh audit data)
py main.py --query "teretana" --city "Subotica" --audit-websites --audit-limit 50
```

### 4. Custom DB path

```bash
py main.py --query "teretana" --city "Subotica" --db "data/my.db"
```

---

## Output

After each run you get:

```
Scraping completed.
Results found:        150
New leads:            82
Existing updated:     61
Duplicates skipped:   7
Invalid records:      3
Websites audited:     20
Audit failures:       2

Export:   exports/hubspot_companies.csv
Database: data/leads.db (142 total)
Scrape runs: 3
```

- **Database:** `data/leads.db`
- **Export:** `exports/hubspot_companies.csv` (UTF-8 with BOM, `;` safe, `čćšžđ` preserved)

---

## CSV / HubSpot

### Columns

`config.py:CSV_COLUMNS` — stable, in order:

```
Company Name, Category, City, Address, Phone, Website, Google Maps URL,
Rating, Review Count, Instagram, Facebook,
First Scraped At, Last Scraped At, Scraped At (alias for Last),
Source Query, Source City,
Lead Status, Lead Score,
Website Opportunity Score, Website Score (alias),
SEO Opportunity Score, SEO Score (alias),
Conversion Opportunity Score, Conversion Score (alias),
Automated Audit Status, Audit Status (manual), Notes
```

- `Website Score` / `SEO Score` / `Conversion Score` are **aliases** kept for backwards compatibility — they mirror the `* Opportunity Score` columns.
- `Scraped At` mirrors `Last Scraped At`.

### HubSpot mapping

Create custom Company properties **before** import (`Settings → Properties → Company → Create property`):

| CSV column | HubSpot property | Type | Purpose |
|---|---|---|---|
| `Google Maps URL` | `google_maps_url` | Text | dedup |
| `Rating` | `google_rating` | Number | 0–5 |
| `Review Count` | `google_review_count` | Number |  |
| `Instagram` | `instagram_url` | Text |  |
| `Facebook` | `facebook_url` | Text |  |
| `First Scraped At` | `first_scraped_at` | Date | |
| `Last Scraped At` | `last_scraped_at` | Date | |
| `Source Query` | `source_query` | Text |  |
| `Source City` | `source_city` | Text |  |
| `Lead Status` | `lead_status_zeltro` | Dropdown | New, Contacted, Qualified, Disqualified |
| `Lead Score` | `lead_score_zeltro` | Number 0–10 | |
| `Website Opportunity Score` | `website_opportunity_score` | Number | alias `website_score` |
| `SEO Opportunity Score` | `seo_opportunity_score` | Number | |
| `Conversion Opportunity Score` | `conversion_opportunity_score` | Number | |
| `Automated Audit Status` | `automated_audit_status` | Dropdown | Not Started, Completed, Unable to Audit |
| `Audit Status` | `audit_status` | Dropdown | Not Started, In Progress, Completed (manual) |
| `Notes` | `notes_zeltro` | Text | |

Standard fields map directly: `Company Name → name`, `City → city`, `Address → address`, `Phone → phone`, `Website → website`.

**Recommended HubSpot workflow** (keep it simple):

1. **Company** — every scraped business (`hubspot_companies.csv`).
2. **Contact** — when you identify a person (owner/reception), create Contact and associate to Company. *Don’t auto-create.*
3. **Deal** — only when there’s a qualified opportunity (e.g., `Power Gym — Website + SEO`). *Don’t create a Deal per Company.*

For a future direct API integration, `exporter.py` is designed to grow a `HubSpotExporter` alongside `CSVExporter` without changing scraper/DB logic (`POST /crm/v3/objects/companies`).

---

## Database & Incremental

- **First run:** `first_scraped_at = last_scraped_at = now()`
- **Re-scrape of existing lead:** `first_scraped_at` stays, `last_scraped_at = now()`; mutable scraped fields (`address`, `phone`, `website`, `rating`, `review_count`, `instagram`, `facebook`, etc.) update; **protected fields** (`lead_status`, `audit_status`, `notes`) never change.

`source_query`/`source_city` keep the first query that found the lead — revisiting via `"fitness"` doesn’t duplicate it.

`scrape_runs` logs each run: `started_at`, `finished_at`, `query`, `city`, `number_found/new/updated/duplicates/invalid`.

Migrations are automatic on `init_db()` — adding `first_scraped_at` etc. never loses the 77 migrated leads.

---

## Deduplication

Priority (highest → lowest) in `deduplication.py:find_existing`:

1. `place_id` (exact)
2. Normalized `Google Maps URL` (`https://` + strip `www.`/`/`)
3. Normalized `phone` (+ address similarity check: if addresses differ with similarity < 0.70 → treat as separate locations even if phone matches)
4. Exact `name + address` (normalized)
5. Fuzzy `name` (>0.85) + `city` match or `address` similarity (>0.80) — but if both have addresses and similarity < 0.70 → not a duplicate (different physical locations)

Multiple `NULL` `place_id` values never collide (empty string stored as `NULL`).

---

## Scoring

All in `scoring.py` — weights easy to change, no logic in `main.py`.

- **Website Opportunity:** `10` = no website, `7` = outdated platform, `2` = modern (known) site.
- **SEO Opportunity:** needs audit; `None` if no audit, `10` if no site, otherwise missing `title` +4, `meta` +3, `h1` +2, `viewport` +1 (cap 10).
- **Conversion Opportunity:** needs audit; missing `tel`/`form`/`booking`/`CTA` etc. sum to 10.
- **Business score:** placeholder (`None`) — add rating/reviews/social rules later if you want.
- **Lead Score:** weighted avg — website ×2, SEO ×1, conversion ×1.

If data isn’t available to judge reliably, the score stays empty/`Not Evaluated` — no guessing.

---

## Website Audit

`website_audit.py:audit_website(url)` — lightweight, not Lighthouse:

- Checks: HTTPS, HTTP status, response time, `title`, `meta description`, `H1`, `viewport`, `canonical`, `robots.txt`, `sitemap.xml`, `tel:`/`mailto:`/form/booking/CTA/offer/Instagram/Facebook/Maps links
- `timeout=10`, realistic headers, redirect limit, `500k` HTML cap, small delay between requests
- On failure: `automated_audit_status = Unable to Audit`, logs `Website audit failed for X: timeout` and continues — one slow site never blocks the run
- Manual vs automated audit are separate (`automated_audit_status` vs `audit_status`)

---

## Validation

`validators.py:validate_lead` checks: `company_name` not empty, `phone` 8–15 digits, `URL` parse, `rating` 0–5, `review_count` int≥0, UTF-8/CSV escaping. One bad lead never crashes the run — it’s logged and skipped.

---

## Testing

```bash
py test_suite.py
```

Covers:
- Same `place_id` → 1 lead
- Same `Google Maps URL` (different formatting) → 1
- Same phone + same/similar address → 1; same phone + clearly different address → 2 (multi-location)
- `NULL place_id` → no UNIQUE collision
- Protected fields survive re-scrape while `rating`/`review_count` update
- `first_scraped_at` frozen, `last_scraped_at` moves
- Opportunity scoring & alias sync
- `Unable to Audit` handling
- CSV stable columns / UTF-8 / comma+quote escaping / source query preservation

---

## .gitignore

Add at repo root:

```gitignore
data/
exports/
__pycache__/
*.pyc
.venv/
```

Commit `config.py` / `scraper.py` / etc., never commit `data/leads.db` or generated CSVs if they contain private leads.

---

## Upload to GitHub

```bash
cd google-maps-scraper
git init
git add config.py database.py validators.py deduplication.py scoring.py scraper.py website_audit.py exporter.py main.py README.md .gitignore
git commit -m "initial: modular scraper with SQLite + HubSpot export"
git branch -M main
git remote add origin https://github.com/<you>/google-maps-scraper.git
git push -u origin main
```

---

## Roadmap (not yet implemented)

- Direct HubSpot API exporter (`HubSpotExporter`) — same dedup before `POST`
- Optional `requirements.txt` / `pyproject.toml`
- More business scoring rules once you define them

---

## License

Personal tool — add a license if you open-source it (MIT recommended).
