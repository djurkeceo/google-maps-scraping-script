"""
Zeltro Lead Scraper — Google Maps → SQLite → HubSpot CSV
==========================================================
Workflow:
  Google Maps scraping → čišćenje → validacija → deduplikacija
  → lead scoring → SQLite (incremental) → website audit → HubSpot CSV export

Primeri:
  py main.py --query "teretana" --city "Subotica" --max-results 30
  py main.py --query "restorani" --city "Subotica" --headless
  py main.py --queries "teretana" "fitness" --city "Subotica" --headless
  py main.py --export-only
  py main.py --migrate-csv
  py main.py --audit-websites
  py main.py --audit-websites --audit-limit 20
  py main.py --export-only --audit-websites
"""

import argparse
import asyncio
import sys
from pathlib import Path

from config import DEFAULT_SEARCHES, DEFAULT_MAX_RESULTS, DB_PATH, EXPORTS_DIR, CSV_COLUMNS
from database import get_connection, init_db, upsert_lead, create_scrape_run, finish_scrape_run
from deduplication import deduplicate_batch, find_existing
from exporter import export_companies_csv, export_summary_info
from scraper import scrape_google_maps
from validators import validate_lead, clean_lead
from scoring import score_lead


def parse_args():
    parser = argparse.ArgumentParser(description="Zeltro Lead Scraper - Google Maps -> HubSpot")
    parser.add_argument("--query", type=str, default=None, help="Search query, npr. 'teretana' ili 'gym'")
    parser.add_argument("--city", type=str, default=None, help="Grad, npr. 'Subotica'")
    parser.add_argument("--queries", type=str, nargs="+", default=None, help="Vise query-ja odjednom")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS, help="Max rezultata po pretrazi")
    parser.add_argument("--headless", action="store_true", help="Pokreni browser u headless modu")
    parser.add_argument("--export-only", action="store_true", help="Samo exportuj postojecu bazu u CSV, bez scraping-a")
    parser.add_argument("--migrate-csv", action="store_true", help="Migriraj stare CSV fajlove (zeltro_leads*.csv) u SQLite")
    parser.add_argument("--db", type=str, default=None, help="Putanja do SQLite baze (default: data/leads.db)")
    parser.add_argument("--audit-websites", action="store_true", help="Pokreni website audit nad postojecim leadovima (ne zahteva scraping)")
    parser.add_argument("--audit-limit", type=int, default=50, help="Max websites za audit u jednom run-u (default 50)")
    parser.add_argument("--audit-only", action="store_true", help="Samo audit, bez scraping/exporta (alias)")
    return parser.parse_args()


def build_search_queries(args) -> list[tuple[str, str, str]]:
    """
    Vraća listu (full_query, category, city).
    Prioritet: --queries > --query+city > DEFAULT_SEARCHES
    """
    result = []
    if args.queries:
        for q in args.queries:
            parts = q.strip().split()
            if args.city and args.city.lower() not in q.lower():
                full = f"{q} {args.city}"
                result.append((full, q, args.city))
            else:
                cat = parts[0] if parts else q
                city = parts[-1] if len(parts) > 1 else (args.city or "")
                result.append((q, cat, city))
        return result

    if args.query:
        city = args.city or ""
        full = f"{args.query} {city}".strip() if city else args.query
        cat = args.query.split()[0]
        result.append((full, cat, city))
        return result

    for q in DEFAULT_SEARCHES:
        parts = q.strip().split()
        cat = parts[0] if parts else q
        city = parts[-1] if len(parts) > 1 else ""
        result.append((q, cat, city))
    return result


async def run_scraping(searches: list[tuple[str, str, str]], max_results: int) -> list[dict]:
    all_leads = []
    for full_query, category, city in searches:
        leads = await scrape_google_maps(full_query, category=category, city=city, max_results=max_results)
        all_leads.extend(leads)
        if len(searches) > 1:
            print(f"\n   Pauza 4s pre sledece pretrage...")
            await asyncio.sleep(4)
    return all_leads


def process_leads(leads: list[dict], conn, do_audit: bool = False, audit_limit: int = 0) -> dict:
    """
    Deduplikacija + validacija + upsert + optional website audit.
    Vraća summary dict sa jasnim definicijama:
      - scraped = Raw listings found (sirovo sa Maps)
      - duplicates_in_batch = uklonjeni unutar istog run-a (pre DB)
      - unique = scraped - duplicates_in_batch (prosleđeno na validaciju/DB)
      - new_leads / updated / invalid / skipped = raspodela unique
    """
    summary = {
        "scraped": len(leads),
        "duplicates_in_batch": 0,
        "unique": 0,
        "duplicates_existing": 0,
        "new_leads": 0,
        "updated": 0,
        "invalid": 0,
        "skipped": 0,
        "websites_audited": 0,
        "audit_failures": 0,
    }

    if not leads:
        return summary

    # 1. Deduplikacija unutar batch-a
    unique_leads, dup_in_batch = deduplicate_batch(leads)
    summary["duplicates_in_batch"] = dup_in_batch
    summary["unique"] = len(unique_leads)
    if dup_in_batch:
        print(f"   Duplikati unutar batch-a uklonjeni: {dup_in_batch} (raw {len(leads)} -> unique {len(unique_leads)})")

    # 1b. Optional audit batch (ako je traženo, auditiraj pre upserta da scoring koristi audit)
    if do_audit and unique_leads:
        try:
            from website_audit import audit_leads_batch
            # audit samo one sa website
            unique_leads, audit_stats = audit_leads_batch(unique_leads, max_audit=audit_limit or 50)
            summary["websites_audited"] = audit_stats.get("audited", 0)
            summary["audit_failures"] = audit_stats.get("failures", 0)
            # Re-score sa audit podacima
            for l in unique_leads:
                # score_lead will use audit_data_json
                score_lead(l, audit_data=l.get("audit_data_json"))
        except Exception as e:
            print(f"   [audit] Greska pri batch auditu: {e}")

    # 2. Validacija + scoring + upsert
    for lead in unique_leads:
        lead = clean_lead(lead)
        # scoring ako nije već urađen ili ako je audit dodao nove podatke
        if lead.get("website_opportunity_score") is None and lead.get("website_score") is None:
            lead = score_lead(lead, audit_data=lead.get("audit_data_json"))

        errors = validate_lead(lead)
        if errors:
            if any("Company Name" in e for e in errors):
                summary["invalid"] += 1
                print(f"   Invalid lead '{lead.get('company_name','?')}': {errors}")
                continue
            else:
                print(f"   Upozorenje za '{lead.get('company_name')}': {errors}")

        existing_id = find_existing(conn, lead)
        if existing_id:
            lead["_existing_id"] = existing_id

        result = upsert_lead(conn, lead)
        if result == "inserted":
            summary["new_leads"] += 1
        elif result == "updated":
            summary["updated"] += 1
            summary["duplicates_existing"] += 1
        elif result == "skipped":
            summary["skipped"] += 1

    return summary


def migrate_old_csvs(conn):
    """Migriraj postojeće zeltro_leads*.csv fajlove u SQLite."""
    import csv

    base = Path(__file__).parent
    csv_files = list(base.glob("zeltro_leads*.csv"))
    if not csv_files:
        print("   Nema starih CSV fajlova za migraciju.")
        return

    print(f"\nMigracija {len(csv_files)} CSV fajla...")
    total_migrated = 0

    for csv_path in csv_files:
        print(f"   -> {csv_path.name}")
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    lead = {
                        "company_name": row.get("ime_firme", "") or row.get("Company Name", ""),
                        "category": row.get("kategorija", "") or row.get("Category", ""),
                        "city": row.get("grad", "") or row.get("City", ""),
                        "address": row.get("adresa", "") or row.get("Address", ""),
                        "phone": row.get("telefon", "") or row.get("Phone", ""),
                        "website": row.get("website", "") or row.get("Website", ""),
                        "google_maps_url": row.get("google_maps_url", "") or row.get("Google Maps URL", ""),
                        "place_id": row.get("place_id", ""),
                        "rating": None,
                        "review_count": None,
                        "instagram": "",
                        "facebook": "",
                        "scraped_at": row.get("scraped_at", "") or row.get("Scraped At", ""),
                        "source_query": row.get("kategorija", "") or row.get("Category", ""),
                        "source_city": row.get("grad", "") or row.get("City", ""),
                        "lead_status": "New",
                        "audit_status": "Not Started",
                        "automated_audit_status": "Not Started",
                        "notes": row.get("napomena", "") or row.get("Notes", ""),
                    }
                    if row.get("Rating"):
                        try:
                            lead["rating"] = float(row["Rating"])
                        except ValueError:
                            pass
                    if row.get("Review Count"):
                        try:
                            lead["review_count"] = int(row["Review Count"])
                        except ValueError:
                            pass

                    lead = score_lead(lead)
                    lead = clean_lead(lead)

                    if not lead.get("company_name"):
                        continue

                    existing_id = find_existing(conn, lead)
                    if existing_id:
                        lead["_existing_id"] = existing_id

                    result = upsert_lead(conn, lead)
                    if result == "inserted":
                        total_migrated += 1

            print(f"      Migrirano iz {csv_path.name}")

        except Exception as e:
            print(f"      Greska pri migraciji {csv_path.name}: {e}")

    print(f"   Ukupno migrirano: {total_migrated} leadova")


def run_audit_on_existing(conn, limit: int = 50) -> dict:
    """Auditira postojeće leadove iz baze koji imaju website i nisu auditirani."""
    from scoring import score_lead as rescore
    cur = conn.execute("""
        SELECT * FROM leads
        WHERE website IS NOT NULL AND website != ''
        ORDER BY last_scraped_at DESC, id ASC
        LIMIT ?
    """, (limit * 3,))  # uzmi više, filtriraj one koji nisu Completed
    rows = cur.fetchall()
    # filtriraj samo one gde automated nije Completed ili je stari audit
    to_audit = []
    for r in rows:
        # ako je već Completed skoro, preskoči — ali za audit-websites želimo da auditira sve sa website
        # jednostavno uzmi prvih limit
        d = dict(r)
        # ako je već Unable, preskoči? ne, možda retry
        to_audit.append(d)
        if len(to_audit) >= limit:
            break

    if not to_audit:
        print("   Nema leadova sa website za audit.")
        return {"audited": 0, "failures": 0}

    print(f"\nAuditing {len(to_audit)} websites...")
    try:
        from website_audit import audit_website
        audited = 0
        failures = 0
        for lead in to_audit:
            website = lead.get("website", "")
            try:
                result = audit_website(website, timeout=10)
                # update lead sa audit rezultatima i re-score
                lead_id = lead["id"]
                # re-score sa novim audit podacima
                tmp = dict(lead)
                tmp["audit_data_json"] = result.get("audit_data_json", "")
                tmp["automated_audit_status"] = result.get("automated_audit_status", "Unable to Audit")
                tmp["http_status"] = result.get("http_status")
                tmp["response_time_ms"] = result.get("response_time_ms")
                # izracunaj nove skorove
                rescored = rescore(tmp, audit_data=result.get("audit_data_json"))
                # pripremi update
                updates = {
                    "automated_audit_status": result.get("automated_audit_status", "Unable to Audit"),
                    "audit_data_json": result.get("audit_data_json", ""),
                    "http_status": result.get("http_status"),
                    "response_time_ms": result.get("response_time_ms"),
                    "website_opportunity_score": rescored.get("website_opportunity_score"),
                    "seo_opportunity_score": rescored.get("seo_opportunity_score"),
                    "conversion_opportunity_score": rescored.get("conversion_opportunity_score"),
                    "lead_score": rescored.get("lead_score"),
                    # sync stare
                    "website_score": rescored.get("website_score"),
                    "seo_score": rescored.get("seo_score"),
                    "conversion_score": rescored.get("conversion_score"),
                }
                # direktan update (ne diramo protected)
                set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
                conn.execute(f"UPDATE leads SET {set_clause}, updated_at=datetime('now') WHERE id=?", list(updates.values()) + [lead_id])
                conn.commit()
                if result.get("automated_audit_status") == "Completed":
                    audited += 1
                    print(f"   Audited {lead['company_name'][:30]} -> SEO:{updates['seo_opportunity_score']} Conv:{updates['conversion_opportunity_score']}")
                else:
                    failures += 1
                    print(f"   Audit failed for {lead['company_name'][:30]}: {result.get('error')}")
            except Exception as e:
                failures += 1
                print(f"   Audit error for {lead['company_name']}: {e}")

        return {"audited": audited, "failures": failures}
    except Exception as e:
        print(f"   [audit] Greska: {e}")
        return {"audited": 0, "failures": 0}


async def main():
    args = parse_args()

    if args.headless:
        import config
        config.PLAYWRIGHT_HEADLESS = True

    db_path = Path(args.db) if args.db else DB_PATH

    print("=" * 55)
    print("  ZELTRO LEAD SCRAPER - Google Maps -> SQLite -> HubSpot")
    print("=" * 55)
    print(f"  DB: {db_path}")
    print(f"  Exports: {EXPORTS_DIR}")

    init_db(db_path)
    conn = get_connection(db_path)

    # alias za audit-only
    if args.audit_only:
        args.audit_websites = True
        args.export_only = True

    summary = {"scraped": 0, "unique": 0, "new_leads": 0, "updated": 0, "duplicates_in_batch": 0, "duplicates_existing": 0, "invalid": 0, "skipped": 0, "websites_audited": 0, "audit_failures": 0}
    run_id = None

    try:
        if args.migrate_csv:
            migrate_old_csvs(conn)

        # audit-only pre scraping
        if args.audit_websites and args.export_only:
            audit_stats = run_audit_on_existing(conn, limit=args.audit_limit)
            summary["websites_audited"] = audit_stats.get("audited", 0)
            summary["audit_failures"] = audit_stats.get("failures", 0)

        if args.export_only and not args.audit_websites:
            print("\nExport-only mod — preskacem scraping")
        elif not args.export_only:
            searches = build_search_queries(args)
            print(f"\nPretrage: {[s[0] for s in searches]}")
            print(f"   Max po pretrazi: {args.max_results}")

            # scrape run tracking
            try:
                run_id = create_scrape_run(conn, query=", ".join([s[0] for s in searches]), city=args.city or "", max_results=args.max_results)
            except Exception:
                run_id = None

            leads = await run_scraping(searches, args.max_results)
            print(f"\nScraping zavrsen: {len(leads)} sirovih leadova")

            do_audit = args.audit_websites
            summary = process_leads(leads, conn, do_audit=do_audit, audit_limit=args.audit_limit)

            if run_id:
                try:
                    finish_scrape_run(conn, run_id, summary)
                except Exception as e:
                    print(f"   [run] Greska pri zatvaranju run-a: {e}")

            # ako je audit tražen a nije urađen u process_leads (npr. 0 leads), odradi naknadno
            if args.audit_websites and summary.get("websites_audited", 0) == 0 and summary.get("scraped", 0) == 0:
                audit_stats = run_audit_on_existing(conn, limit=args.audit_limit)
                summary["websites_audited"] = audit_stats.get("audited", 0)
                summary["audit_failures"] = audit_stats.get("failures", 0)

        # ako nije export-only audit, a audit je tražen nakon scraping-a, osveži audit za postojeće ako treba
        if args.audit_websites and not args.export_only and summary.get("websites_audited", 0) == 0:
            # već je audit urađen u process_leads batch-u, nema dodatnog
            pass

        # Export
        export_path = export_companies_csv(conn)
        print(f"\nExport: {export_path}")

        # Summary — jasne definicije, logički povezivo
        raw = summary.get('scraped', 0)
        batch_dup = summary.get('duplicates_in_batch', 0)
        unique = summary.get('unique', raw - batch_dup)
        # fallback ako unique nije postavljen (export-only run)
        if unique == 0 and raw == 0:
            unique = 0
        print("\n" + "=" * 55)
        print("  ZAVRSENO")
        print("=" * 55)
        print(f"  Scraping completed.")
        print(f"  Raw listings found:       {raw}")
        print(f"  Batch duplicates removed: {batch_dup}")
        print(f"  Unique listings:          {unique}")
        print(f"  New leads:                {summary['new_leads']}")
        print(f"  Existing leads updated:   {summary['updated']}")
        print(f"  Invalid records:          {summary['invalid']}")
        # audit stats
        if args.audit_websites or summary.get("websites_audited", 0) > 0 or summary.get("audit_failures", 0) > 0:
            print(f"  Websites audited:         {summary.get('websites_audited', 0)}")
            print(f"  Audit failures:           {summary.get('audit_failures', 0)}")
        else:
            print(f"  Websites audited:         N/A (use --audit-websites)")
        # konzistentnost provera (debug, ne remeti)
        # unique bi trebalo da bude new+updated+invalid+skipped
        print(f"\n  Export:   {export_path}")
        print(f"  Database: {db_path} ({conn.execute('SELECT COUNT(*) FROM leads').fetchone()[0]} ukupno)")
        # scrape_runs info
        try:
            runs = conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0]
            if runs > 0:
                print(f"  Scrape runs: {runs}")
        except Exception:
            pass
        print("=" * 55)

        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        if total > 0:
            print(export_summary_info())

        print("\n  Database updated successfully.")

    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
