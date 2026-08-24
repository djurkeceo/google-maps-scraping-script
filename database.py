"""
database.py — SQLite kao interni storage, CSV je samo export
- jedna tabela: leads
- unique constraint na place_id (kad postoji, NULL dozvoljeno više puta)
- fallback deduplikacija preko aplikacijske logike (deduplication.py)
- incremental: postojeći leadovi se UPDATE-uju (scraped polja), manual polja se ne diraju
- migracije: first/last scraped, source_query/city, opportunity skorovi, audit status
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH

# Kolone koje smeju da se automatski ažuriraju pri re-scrape-u (scraped data)
SCRAPED_FIELDS = [
    "company_name",
    "category",
    "city",
    "address",
    "phone",
    "website",
    "google_maps_url",
    "rating",
    "review_count",
    "instagram",
    "facebook",
    "place_id",
    "source_query",
    "source_city",
]

# Polja koja NIKAD ne prebrisujemo automatski (ručno unesena / sales)
PROTECTED_FIELDS = [
    "lead_status",
    "audit_status",  # manual audit
    "notes",
]

# Scoring polja — ažuriraju se samo ako je novi skor izračunat (nije prazan)
# Podržavamo i stare i nove nazive (opportunity)
SCORING_FIELDS = [
    "lead_score",
    "website_score",
    "seo_score",
    "conversion_score",
    "website_opportunity_score",
    "seo_opportunity_score",
    "conversion_opportunity_score",
]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT UNIQUE,
    company_name TEXT NOT NULL,
    category TEXT,
    city TEXT,
    address TEXT,
    phone TEXT,
    website TEXT,
    google_maps_url TEXT,
    rating REAL,
    review_count INTEGER,
    instagram TEXT,
    facebook TEXT,
    scraped_at TEXT,
    lead_status TEXT DEFAULT 'New',
    lead_score INTEGER,
    website_score INTEGER,
    seo_score INTEGER,
    conversion_score INTEGER,
    audit_status TEXT DEFAULT 'Not Started',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_leads_company_city ON leads(company_name, city);",
    "CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);",
    "CREATE INDEX IF NOT EXISTS idx_leads_google_maps_url ON leads(google_maps_url);",
    "CREATE INDEX IF NOT EXISTS idx_leads_place_id ON leads(place_id);",
]

CREATE_SCRAPE_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    query TEXT,
    city TEXT,
    source_query TEXT,
    max_results INTEGER,
    number_found INTEGER DEFAULT 0,
    number_new INTEGER DEFAULT 0,
    number_updated INTEGER DEFAULT 0,
    number_duplicates INTEGER DEFAULT 0,
    number_invalid INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        if row["name"] == column:
            return True
    return False


def _ensure_migrations(conn: sqlite3.Connection) -> None:
    """Dodaj nove kolone ako ne postoje — sigurna migracija za postojeću bazu."""
    migrations = [
        ("first_scraped_at", "TEXT"),
        ("last_scraped_at", "TEXT"),
        ("source_query", "TEXT"),
        ("source_city", "TEXT"),
        ("automated_audit_status", "TEXT DEFAULT 'Not Started'"),
        ("audit_data_json", "TEXT"),
        ("website_opportunity_score", "INTEGER"),
        ("seo_opportunity_score", "INTEGER"),
        ("conversion_opportunity_score", "INTEGER"),
        ("http_status", "INTEGER"),
        ("response_time_ms", "INTEGER"),
    ]
    for col, typedef in migrations:
        if not _column_exists(conn, "leads", col):
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {typedef}")
            print(f"   [migrated] leads.{col}")

    # Kreiraj scrape_runs tabelu ako ne postoji
    conn.execute(CREATE_SCRAPE_RUNS_SQL)

    # Migriraj postojeće podatke: scraped_at -> first/last
    # i website_score -> opportunity score ako su null
    try:
        # Proveri da li ima redova sa scraped_at a bez first/last
        cur = conn.execute("SELECT COUNT(*) FROM leads WHERE first_scraped_at IS NULL AND scraped_at IS NOT NULL")
        cnt = cur.fetchone()[0]
        if cnt > 0:
            conn.execute("""
                UPDATE leads
                SET first_scraped_at = COALESCE(first_scraped_at, scraped_at, created_at),
                    last_scraped_at = COALESCE(last_scraped_at, scraped_at, updated_at)
                WHERE first_scraped_at IS NULL
            """)
            print(f"   [migrated] {cnt} leads: scraped_at -> first/last_scraped_at")
        # Sync opportunity skorova
        cur = conn.execute("SELECT COUNT(*) FROM leads WHERE website_opportunity_score IS NULL AND website_score IS NOT NULL")
        cnt2 = cur.fetchone()[0]
        if cnt2 > 0:
            conn.execute("""
                UPDATE leads
                SET website_opportunity_score = COALESCE(website_opportunity_score, website_score),
                    seo_opportunity_score = COALESCE(seo_opportunity_score, seo_score),
                    conversion_opportunity_score = COALESCE(conversion_opportunity_score, conversion_score)
                WHERE website_opportunity_score IS NULL
            """)
            print(f"   [migrated] {cnt2} leads: sync opportunity scores")
        conn.commit()
    except Exception as e:
        print(f"   [migration warning] {e}")


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(CREATE_TABLE_SQL)
        for sql in CREATE_INDEXES_SQL:
            conn.execute(sql)
        conn.execute(CREATE_SCRAPE_RUNS_SQL)
        conn.commit()
        _ensure_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_lead(conn: sqlite3.Connection, lead: dict) -> str:
    """
    Ubaci novi lead ili ažuriraj postojeći.
    Deduplikacija se rešava PRE poziva ove funkcije kroz find_existing().
    Vraća: 'inserted' | 'updated' | 'skipped'
    """
    if not lead.get("company_name"):
        return "skipped"

    # Osiguraj migracije (ako je baza stara i pozvan direktno)
    _ensure_migrations(conn)

    existing = None
    if lead.get("place_id"):
        cur = conn.execute("SELECT * FROM leads WHERE place_id = ?", (lead["place_id"],))
        existing = cur.fetchone()

    if existing is None and lead.get("_existing_id"):
        cur = conn.execute("SELECT * FROM leads WHERE id = ?", (lead["_existing_id"],))
        existing = cur.fetchone()

    now = _now_iso()

    if existing is None:
        # INSERT — normalizuj prazne place_id / url u None da ne krši UNIQUE
        if lead.get("place_id") == "":
            lead["place_id"] = None
        if lead.get("google_maps_url") == "":
            lead["google_maps_url"] = None

        # first/last scraped
        ts = lead.get("last_scraped_at") or lead.get("scraped_at") or now
        lead["first_scraped_at"] = lead.get("first_scraped_at") or ts
        lead["last_scraped_at"] = ts
        # za backward compat, scraped_at = last
        lead["scraped_at"] = ts

        lead["created_at"] = now
        lead["updated_at"] = now
        lead.setdefault("lead_status", "New")
        lead.setdefault("audit_status", "Not Started")
        lead.setdefault("automated_audit_status", "Not Started")

        # Sync opportunity skorova sa starim ako nisu postavljeni
        if lead.get("website_opportunity_score") is not None and lead.get("website_score") is None:
            lead["website_score"] = lead["website_opportunity_score"]
        if lead.get("website_score") is not None and lead.get("website_opportunity_score") is None:
            lead["website_opportunity_score"] = lead["website_score"]
        if lead.get("seo_opportunity_score") is not None and lead.get("seo_score") is None:
            lead["seo_score"] = lead["seo_opportunity_score"]
        if lead.get("seo_score") is not None and lead.get("seo_opportunity_score") is None:
            lead["seo_opportunity_score"] = lead["seo_score"]
        if lead.get("conversion_opportunity_score") is not None and lead.get("conversion_score") is None:
            lead["conversion_score"] = lead["conversion_opportunity_score"]
        if lead.get("conversion_score") is not None and lead.get("conversion_opportunity_score") is None:
            lead["conversion_opportunity_score"] = lead["conversion_score"]

        lead.pop("_existing_id", None)
        cols = [k for k in lead.keys() if k in _all_columns()]
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})"
        try:
            conn.execute(sql, [lead[c] for c in cols])
            conn.commit()
            return "inserted"
        except sqlite3.IntegrityError as e:
            # ako je place_id unique collision, probaj da nadjes postojeci
            print(f"   [db] IntegrityError on insert: {e} — lead={lead.get('company_name')}")
            return "skipped"
    else:
        # UPDATE — samo scraped + scoring + audit automation, ne diraj protected
        updates = {}
        for field in SCRAPED_FIELDS:
            new_val = lead.get(field)
            if field in ("rating", "review_count"):
                if new_val is not None and new_val != "":
                    updates[field] = new_val
            else:
                if new_val not in (None, ""):
                    # source_query/city: ne prepisuj ako vec postoji
                    if field in ("source_query", "source_city"):
                        if not existing[field]:
                            updates[field] = new_val
                        # ako postoji, ne diraj (prvi source ostaje)
                        continue
                    if new_val != existing[field]:
                        updates[field] = new_val
                    elif not existing[field] and new_val:
                        updates[field] = new_val

        # Scoring — ažuriraj samo ako je novi skor not None/not ""
        for field in SCORING_FIELDS:
            new_val = lead.get(field)
            if new_val not in (None, ""):
                updates[field] = new_val
                # sync old/new opportunity
                if field == "website_opportunity_score" and "website_score" not in updates:
                    updates["website_score"] = new_val
                if field == "website_score" and "website_opportunity_score" not in updates:
                    updates["website_opportunity_score"] = new_val
                if field == "seo_opportunity_score" and "seo_score" not in updates:
                    updates["seo_score"] = new_val
                if field == "seo_score" and "seo_opportunity_score" not in updates:
                    updates["seo_opportunity_score"] = new_val
                if field == "conversion_opportunity_score" and "conversion_score" not in updates:
                    updates["conversion_score"] = new_val
                if field == "conversion_score" and "conversion_opportunity_score" not in updates:
                    updates["conversion_opportunity_score"] = new_val

        # Automated audit polja — dozvoli update ako je audit rađen
        for af in ("automated_audit_status", "audit_data_json", "http_status", "response_time_ms"):
            if lead.get(af) not in (None, ""):
                # ne prepisuj manual audit_status
                if af == "automated_audit_status":
                    updates[af] = lead[af]
                elif af in ("audit_data_json", "http_status", "response_time_ms"):
                    updates[af] = lead[af]

        # last_scraped_at uvek osveži, first ne diraj
        updates["last_scraped_at"] = lead.get("last_scraped_at") or lead.get("scraped_at") or now
        updates["scraped_at"] = updates["last_scraped_at"]
        updates["updated_at"] = now

        if not updates:
            return "skipped"

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        sql = f"UPDATE leads SET {set_clause} WHERE id = ?"
        conn.execute(sql, list(updates.values()) + [existing["id"]])
        conn.commit()
        return "updated"


def _all_columns() -> set:
    return {
        "place_id", "company_name", "category", "city", "address", "phone",
        "website", "google_maps_url", "rating", "review_count", "instagram",
        "facebook", "scraped_at", "first_scraped_at", "last_scraped_at",
        "source_query", "source_city",
        "lead_status", "lead_score",
        "website_score", "seo_score", "conversion_score",
        "website_opportunity_score", "seo_opportunity_score", "conversion_opportunity_score",
        "audit_status", "automated_audit_status", "audit_data_json",
        "http_status", "response_time_ms",
        "notes", "created_at", "updated_at",
    }


def fetch_all_leads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM leads ORDER BY id ASC")
    return cur.fetchall()


def fetch_export_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = fetch_all_leads(conn)
    result = []
    for r in rows:
        result.append(dict(r))
    return result


def count_leads(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM leads")
    return cur.fetchone()[0]


# ── Scrape Runs ──

def create_scrape_run(conn: sqlite3.Connection, query: str = "", city: str = "", max_results: int = 0) -> int:
    _ensure_migrations(conn)
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO scrape_runs (started_at, query, city, source_query, max_results, status) VALUES (?, ?, ?, ?, ?, 'running')",
        (now, query, city, query, max_results),
    )
    conn.commit()
    return cur.lastrowid


def finish_scrape_run(conn: sqlite3.Connection, run_id: int, stats: dict) -> None:
    now = _now_iso()
    conn.execute(
        """UPDATE scrape_runs SET finished_at=?, number_found=?, number_new=?, number_updated=?,
           number_duplicates=?, number_invalid=?, status='completed' WHERE id=?""",
        (
            now,
            stats.get("scraped", 0),
            stats.get("new_leads", 0),
            stats.get("updated", 0),
            stats.get("duplicates_in_batch", 0) + stats.get("duplicates_existing", 0),
            stats.get("invalid", 0),
            run_id,
        ),
    )
    conn.commit()
