"""
exporter.py — HubSpot kompatibilni CSV export
- jedna tabela: hubspot_companies.csv (dovoljno za MVP)
- kasnije lako dodati contacts/deals export
"""

import csv
import sqlite3
from pathlib import Path

from config import CSV_COLUMNS, EXPORTS_DIR
from scoring import score_to_csv_value


# Mapiranje snake_case (baza) → CSV kolona
# Podrzava i stare i nove nazive (opportunity)
DB_TO_CSV_MAP = {
    "company_name": "Company Name",
    "category": "Category",
    "city": "City",
    "address": "Address",
    "phone": "Phone",
    "website": "Website",
    "google_maps_url": "Google Maps URL",
    "rating": "Rating",
    "review_count": "Review Count",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "first_scraped_at": "First Scraped At",
    "last_scraped_at": "Last Scraped At",
    "scraped_at": "Scraped At",
    "source_query": "Source Query",
    "source_city": "Source City",
    "lead_status": "Lead Status",
    "lead_score": "Lead Score",
    "website_opportunity_score": "Website Opportunity Score",
    "website_score": "Website Score",
    "seo_opportunity_score": "SEO Opportunity Score",
    "seo_score": "SEO Score",
    "conversion_opportunity_score": "Conversion Opportunity Score",
    "conversion_score": "Conversion Score",
    "automated_audit_status": "Automated Audit Status",
    "audit_status": "Audit Status",
    "notes": "Notes",
}


def export_companies_csv(conn: sqlite3.Connection, output_path: Path | None = None) -> Path:
    """
    Exportuje sve leadove iz baze u HubSpot-compatible CSV.
    - UTF-8 sa BOM (utf-8-sig) za srpska slova
    - stabilne kolone iz config.CSV_COLUMNS
    - prazne vrednosti umesto None
    """
    if output_path is None:
        output_path = EXPORTS_DIR / "hubspot_companies.csv"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # sort po lead_score (opportunity) pa rating
    try:
        cur = conn.execute("SELECT * FROM leads ORDER BY lead_score DESC, rating DESC, id ASC")
    except sqlite3.OperationalError:
        cur = conn.execute("SELECT * FROM leads ORDER BY id ASC")
    rows = cur.fetchall()

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()

        for row in rows:
            csv_row: dict[str, str] = {}
            for csv_col in CSV_COLUMNS:
                # nađi db ključ za ovu CSV kolonu
                db_key = None
                for k, v in DB_TO_CSV_MAP.items():
                    if v == csv_col:
                        db_key = k
                        break

                if db_key is None:
                    csv_row[csv_col] = ""
                    continue

                # row.keys() su kolone iz SELECT *
                try:
                    val = row[db_key] if db_key in row.keys() else ""
                except Exception:
                    val = ""

                # sync aliasi: ako je opportunity prazno a staro nije, koristi staro
                if csv_col == "Website Opportunity Score" and (val is None or val == ""):
                    try:
                        fallback = row["website_score"] if "website_score" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass
                if csv_col == "Website Score" and (val is None or val == ""):
                    try:
                        fallback = row["website_opportunity_score"] if "website_opportunity_score" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass
                if csv_col == "SEO Opportunity Score" and (val is None or val == ""):
                    try:
                        fallback = row["seo_score"] if "seo_score" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass
                if csv_col == "SEO Score" and (val is None or val == ""):
                    try:
                        fallback = row["seo_opportunity_score"] if "seo_opportunity_score" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass
                if csv_col == "Conversion Opportunity Score" and (val is None or val == ""):
                    try:
                        fallback = row["conversion_score"] if "conversion_score" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass
                if csv_col == "Conversion Score" and (val is None or val == ""):
                    try:
                        fallback = row["conversion_opportunity_score"] if "conversion_opportunity_score" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass
                # Scraped At alias za Last
                if csv_col == "Scraped At" and (val is None or val == ""):
                    try:
                        fallback = row["last_scraped_at"] if "last_scraped_at" in row.keys() else ""
                        if fallback not in (None, ""):
                            val = fallback
                    except Exception:
                        pass

                # Scoring kolone: None → prazno
                if csv_col in ("Lead Score", "Website Score", "SEO Score", "Conversion Score",
                               "Website Opportunity Score", "SEO Opportunity Score", "Conversion Opportunity Score"):
                    if val is None or val == "":
                        csv_row[csv_col] = ""
                    else:
                        csv_row[csv_col] = str(val)
                elif csv_col in ("Rating", "Review Count"):
                    csv_row[csv_col] = "" if val is None else str(val)
                else:
                    csv_row[csv_col] = "" if val is None else str(val)

            writer.writerow(csv_row)

    return output_path


def export_summary_info() -> str:
    """
    Uputstvo za HubSpot import — vraća tekst koji se prikazuje korisniku.
    """
    return """
HubSpot Import Uputstvo:
------------------------
1. Idi na HubSpot -> Contacts -> Companies -> Import
2. Izaberi 'File from computer' -> 'One file' -> 'Multiple objects' (ako zelis samo Companies, izaberi 'One object')
3. Upload: exports/hubspot_companies.csv
4. Mapiraj kolone prema HubSpot properties:

   Standardna HubSpot polja (vec postoje):
     Company Name  -> Company Name (name)
     City          -> City (city)
     Address       -> Address (address)
     Phone         -> Phone Number (phone)
     Website       -> Website Domain (website)

   Custom properties — KREIRAJ RUCNO pre importa (Settings -> Properties -> Company -> Create property):
     Google Maps URL  -> google_maps_url  (Text)
     Rating           -> google_rating    (Number)
     Review Count     -> google_review_count (Number)
     Instagram        -> instagram_url    (Text)
     Facebook         -> facebook_url     (Text)
     First Scraped At -> first_scraped_at (Date)
     Last Scraped At  -> last_scraped_at  (Date)
     Source Query     -> source_query     (Text)
     Source City      -> source_city      (Text)
     Lead Status      -> lead_status_zeltro (Dropdown: New, Contacted, Qualified, Disqualified)
     Lead Score       -> lead_score_zeltro  (Number 0-10)
     Website Opportunity Score -> website_opportunity_score (Number 0-10) [alias: website_score]
     SEO Opportunity Score     -> seo_opportunity_score     (Number 0-10) [alias: seo_score]
     Conversion Opportunity Score -> conversion_opportunity_score (Number 0-10)
     Automated Audit Status -> automated_audit_status (Dropdown: Not Started, Completed, Unable to Audit)
     Audit Status (manual)  -> audit_status (Dropdown: Not Started, In Progress, Completed)
     Category         -> category         (Text ili Dropdown)
     Notes            -> notes_zeltro     (Text)

   Napomena: stari nazivi (Website Score, SEO Score) su aliasi za nove opportunity skorove.
   Novi export sadrzi obe kolone sa istom vrednoscu radi kompatibilnosti.

5. Workflow predlog:
     Scraped biznis -> Company (ovaj CSV)
     Kad nadjes kontakt osobu -> rucno dodaj Contact i povezi sa Company
     Kad postoji prilika -> kreiraj Deal i povezi sa Company + Contact

   Nemoj automatski kreirati Deal za svaki Company — to zagadjuje pipeline.
   Deal se kreira tek kad je lead kontaktiran i kvalifikovan.

6. Za direktnu API integraciju (kasnije):
     - Umesto CSV-a, koristi hubspot-api-client (pip install hubspot-api-client)
     - Endpoint: POST /crm/v3/objects/companies
     - Deduplikacija po google_maps_url ili phone pre slanja
"""
