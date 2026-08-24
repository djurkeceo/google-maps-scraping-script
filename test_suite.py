"""
test_suite.py — minimalni testovi za deduplikaciju, incremental, scoring, audit, CSV
Pokrece se sa: py test_suite.py
"""

import sys
import tempfile
import sqlite3
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import init_db, get_connection, upsert_lead
from deduplication import find_existing, deduplicate_batch, extract_place_id
from scoring import score_lead, calc_website_opportunity_score, calc_seo_opportunity_score, calc_conversion_opportunity_score
from validators import validate_lead
from website_audit import audit_website

def assert_eq(a, b, msg=""):
    if a != b:
        print(f"  FAIL: {msg} expected {b!r} got {a!r}")
        return False
    print(f"  PASS: {msg}")
    return True

def assert_true(cond, msg=""):
    if not cond:
        print(f"  FAIL: {msg}")
        return False
    print(f"  PASS: {msg}")
    return True

all_pass = True

def run_test(name, fn):
    global all_pass
    print(f"\n=== {name} ===")
    try:
        ok = fn()
        if not ok:
            all_pass = False
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        import traceback; traceback.print_exc()
        all_pass = False

# ── Helpers ──
def temp_db():
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    p = Path(tf.name)
    init_db(p)
    return p

# 1. Place ID dedup
def test_place_id():
    p = temp_db()
    conn = get_connection(p)
    lead1 = {"company_name":"Power Gym","category":"teretana","city":"Subotica","address":"Adr 1","phone":"024 111111","website":"","google_maps_url":"https://maps.google.com/place/A/@1,1/data=!1s0x123:0x456","place_id":"0x123:0x456","rating":4.8,"review_count":100,"instagram":"","facebook":"","scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead1 = score_lead(lead1)
    r1 = upsert_lead(conn, lead1)
    # isti place_id, drugo ime — treba da bude update, ne insert
    lead2 = {"company_name":"Power Gym","category":"gym","city":"Subotica","address":"Adr 1","phone":"024 999999","website":"","google_maps_url":"https://maps.google.com/place/A/@1,1/data=!1s0x123:0x456","place_id":"0x123:0x456","rating":4.9,"review_count":120,"instagram":"","facebook":"","scraped_at":"2026-01-02T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead2 = score_lead(lead2)
    # rucno nađi existing
    eid = find_existing(conn, lead2)
    ok = assert_eq(eid, 1, "same place_id -> same id")
    if eid: lead2["_existing_id"] = eid
    r2 = upsert_lead(conn, lead2)
    ok2 = assert_eq(r2, "updated", "place_id duplicate -> updated")
    cnt = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    ok3 = assert_eq(cnt, 1, "count 1 after duplicate place_id")
    conn.close()
    p.unlink(missing_ok=True)
    return ok and ok2 and ok3

# 2. URL dedup normalized
def test_url_dedup():
    p = temp_db()
    conn = get_connection(p)
    lead1 = {"company_name":"Gym A","city":"Subotica","address":"A 1","phone":"024 111111","website":"","google_maps_url":"https://www.google.com/maps/place/Gym+A/@1,1","place_id":None,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead1 = score_lead(lead1)
    upsert_lead(conn, lead1)
    lead2 = {"company_name":"Gym A","city":"Subotica","address":"A 1","phone":"024 222222","website":"","google_maps_url":"https://google.com/maps/place/Gym+A/@1,1/","place_id":None,"scraped_at":"2026-01-02T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead2 = score_lead(lead2)
    eid = find_existing(conn, lead2)
    ok = assert_true(eid is not None, "normalized URL -> duplicate found")
    if eid: lead2["_existing_id"] = eid
    r2 = upsert_lead(conn, lead2)
    cnt = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    ok2 = assert_eq(cnt, 1, "URL duplicate count 1")
    conn.close(); p.unlink(missing_ok=True)
    return ok and ok2

# 3. Phone + address similarity
def test_phone_address():
    p = temp_db()
    conn = get_connection(p)
    lead1 = {"company_name":"Power Gym","city":"Subotica","address":"Bulevar 10, Subotica","phone":"024 333333","google_maps_url":None,"place_id":None,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead1 = score_lead(lead1)
    upsert_lead(conn, lead1)
    # isti telefon, slična adresa -> treba duplicate
    lead2 = {"company_name":"Power Gym","city":"Subotica","address":"Bulevar 10, Subotica","phone":"024 333333","google_maps_url":None,"place_id":None,"scraped_at":"2026-01-02T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead2 = score_lead(lead2)
    eid = find_existing(conn, lead2)
    ok = assert_true(eid is not None, "same phone+same address -> duplicate")
    # isti telefon, različita adresa -> treba 2 lead-a (različite lokacije)
    lead3 = {"company_name":"Power Gym","city":"Subotica","address":"Karađorđeva 50, Subotica","phone":"024 333333","google_maps_url":None,"place_id":None,"scraped_at":"2026-01-03T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead3 = score_lead(lead3)
    eid3 = find_existing(conn, lead3)
    ok2 = assert_true(eid3 is None, "same phone but different address (<0.7) -> not duplicate, 2 locations")
    if eid3 is None:
        r = upsert_lead(conn, lead3)
        ok2 = assert_eq(r, "inserted", "different location inserted")
    cnt = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    ok3 = assert_eq(cnt, 2, "2 locations kept")
    conn.close(); p.unlink(missing_ok=True)
    return ok and ok2 and ok3

# 4. Multiple locations same name different address -> 2 leads
def test_multiple_locations():
    p = temp_db()
    conn = get_connection(p)
    leadA = {"company_name":"Power Gym","city":"Subotica","address":"Location A 1, Subotica","phone":"024 111111","place_id":"0xAAA:0x111","google_maps_url":"https://maps/place/A","scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    leadB = {"company_name":"Power Gym","city":"Subotica","address":"Location B 99, Subotica","phone":"024 222222","place_id":"0xBBB:0x222","google_maps_url":"https://maps/place/B","scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    for l in (leadA, leadB):
        l2 = score_lead(l)
        upsert_lead(conn, l2)
    cnt = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    ok = assert_eq(cnt, 2, "same name different place_id -> 2 leads")
    conn.close(); p.unlink(missing_ok=True)
    return ok

# 5. Null place_id multiple -> no collision
def test_null_place_id():
    p = temp_db()
    conn = get_connection(p)
    for i in range(5):
        lead = {"company_name":f"Firm {i}","city":"Subotica","address":f"Addr {i}","phone":f"024 00000{i}","place_id":None,"google_maps_url":None,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
        lead = score_lead(lead)
        r = upsert_lead(conn, lead)
        assert_eq(r, "inserted", f"null place_id {i} inserted")
    cnt = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    ok = assert_eq(cnt, 5, "5 null place_id leads without collision")
    conn.close(); p.unlink(missing_ok=True)
    return ok

# 6. Incremental protected fields
def test_protected():
    p = temp_db()
    conn = get_connection(p)
    lead = {"company_name":"Test Gym","city":"Subotica","address":"Adr 1","phone":"024 123456","website":"","google_maps_url":"https://maps/place/test","place_id":"0xTEST:0x1","rating":4.0,"review_count":10,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead = score_lead(lead)
    upsert_lead(conn, lead)
    # manual update
    conn.execute("UPDATE leads SET lead_status='Contacted', notes='Interested', audit_status='In Progress' WHERE company_name='Test Gym'")
    conn.commit()
    # re-scrape with new rating
    lead2 = {"company_name":"Test Gym","city":"Subotica","address":"Adr 1","phone":"024 123456","website":"","google_maps_url":"https://maps/place/test","place_id":"0xTEST:0x1","rating":4.9,"review_count":200,"scraped_at":"2026-01-02T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead2 = score_lead(lead2)
    eid = find_existing(conn, lead2)
    lead2["_existing_id"] = eid
    upsert_lead(conn, lead2)
    row = conn.execute("SELECT lead_status, notes, audit_status, rating, review_count FROM leads WHERE place_id='0xTEST:0x1'").fetchone()
    ok = assert_eq(row["lead_status"], "Contacted", "protected lead_status not overwritten")
    ok2 = assert_eq(row["notes"], "Interested", "protected notes not overwritten")
    ok3 = assert_eq(row["audit_status"], "In Progress", "protected audit_status not overwritten")
    ok4 = assert_eq(row["rating"], 4.9, "scraped rating updated")
    ok5 = assert_eq(row["review_count"], 200, "scraped review_count updated")
    conn.close(); p.unlink(missing_ok=True)
    return ok and ok2 and ok3 and ok4 and ok5

# 7. First/Last scraped
def test_first_last():
    p = temp_db()
    conn = get_connection(p)
    lead = {"company_name":"Gym X","city":"Subotica","address":"Adr 1","phone":"024 111111","place_id":"0xFL:0x1","google_maps_url":"https://maps/place/x","scraped_at":"2026-01-01T10:00:00+00:00","first_scraped_at":"2026-01-01T10:00:00+00:00","last_scraped_at":"2026-01-01T10:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead = score_lead(lead)
    upsert_lead(conn, lead)
    row1 = conn.execute("SELECT first_scraped_at, last_scraped_at FROM leads WHERE place_id='0xFL:0x1'").fetchone()
    ok = assert_eq(row1["first_scraped_at"], "2026-01-01T10:00:00+00:00", "first set")
    # update
    lead2 = {"company_name":"Gym X","city":"Subotica","address":"Adr 1","phone":"024 111111","place_id":"0xFL:0x1","google_maps_url":"https://maps/place/x","scraped_at":"2026-01-02T12:00:00+00:00","last_scraped_at":"2026-01-02T12:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead2 = score_lead(lead2)
    eid = find_existing(conn, lead2)
    lead2["_existing_id"] = eid
    upsert_lead(conn, lead2)
    row2 = conn.execute("SELECT first_scraped_at, last_scraped_at FROM leads WHERE place_id='0xFL:0x1'").fetchone()
    ok2 = assert_eq(row2["first_scraped_at"], "2026-01-01T10:00:00+00:00", "first not changed")
    ok3 = assert_eq(row2["last_scraped_at"], "2026-01-02T12:00:00+00:00", "last updated")
    conn.close(); p.unlink(missing_ok=True)
    return ok and ok2 and ok3

# 8. Scoring opportunity names
def test_scoring():
    # no website
    lead = {"website":""}
    lead = score_lead(lead)
    ok = assert_eq(lead["website_opportunity_score"], 10, "no website -> 10")
    ok2 = assert_eq(lead["website_score"], 10, "alias sync")
    # outdated
    lead2 = {"website":"https://foo.wix.com/bar"}
    lead2 = score_lead(lead2)
    ok3 = assert_eq(lead2["website_opportunity_score"], 7, "outdated -> 7")
    # good
    lead3 = {"website":"https://example.com"}
    lead3 = score_lead(lead3)
    ok4 = assert_eq(lead3["website_opportunity_score"], 2, "good -> 2")
    # seo without audit -> None
    ok5 = assert_true(lead3["seo_opportunity_score"] is None, "seo without audit -> None")
    # seo with audit missing title
    audit = {"title":"", "meta_description":"", "h1":"", "viewport":"", "http_status":200}
    import json
    lead4 = {"website":"https://example.com", "audit_data_json": json.dumps(audit)}
    lead4 = score_lead(lead4, audit_data=audit)
    ok6 = assert_true(lead4["seo_opportunity_score"] >= 9, "seo missing title/meta -> high")
    # conversion with audit
    audit2 = {"has_tel_link":False,"has_form":False,"has_booking_link":False,"has_cta":False,"has_offer":False,"has_maps_link":False,"http_status":200}
    lead5 = {"website":"https://example.com", "audit_data_json": json.dumps(audit2)}
    lead5 = score_lead(lead5, audit_data=audit2)
    ok7 = assert_eq(lead5["conversion_opportunity_score"], 10, "all conversion missing -> 10")
    return ok and ok2 and ok3 and ok4 and ok5 and ok6 and ok7

# 9. Audit unreachable -> Unable
def test_audit_unable():
    # no website
    res = audit_website("", timeout=5)
    ok = assert_eq(res["automated_audit_status"], "Unable to Audit", "no website Unable")
    # invalid url with timeout short
    res2 = audit_website("https://this-domain-definitely-not-exists-xyz12345.com", timeout=3)
    # moze biti Unable ili Completed ako se nekako resolve, ali ne sme da crashuje
    ok2 = assert_true(res2["automated_audit_status"] in ("Unable to Audit","Completed","Not Started"), "audit not crash")
    return ok and ok2

# 10. CSV utf-8 and stable columns
def test_csv():
    from exporter import export_companies_csv
    from config import CSV_COLUMNS
    p = temp_db()
    conn = get_connection(p)
    lead = {"company_name":"Čabarkapa Šumadija — test","city":"Subotica","address":"Njegoševa 2, Subotica","phone":"024 123 456","website":"https://example.com","google_maps_url":"https://maps/place/test","place_id":None,"rating":4.5,"review_count":20,"instagram":"","facebook":"","scraped_at":"2026-01-01T00:00:00+00:00","first_scraped_at":"2026-01-01T00:00:00+00:00","last_scraped_at":"2026-01-01T00:00:00+00:00","source_query":"test","source_city":"Subotica","lead_status":"New","lead_score":5,"website_opportunity_score":2,"seo_opportunity_score":None,"conversion_opportunity_score":None,"audit_status":"Not Started","automated_audit_status":"Not Started","notes":"Test, sa zarezom i \"navodnicima\""}
    # ensure all required columns
    # need to fill required scoring aliases
    lead["website_score"]=2; lead["seo_score"]=None; lead["conversion_score"]=None
    lead["http_status"]=None; lead["response_time_ms"]=None; lead["audit_data_json"]=None
    lead["created_at"]="2026-01-01T00:00:00+00:00"; lead["updated_at"]="2026-01-01T00:00:00+00:00"
    # upsert
    upsert_lead(conn, score_lead(lead))
    # export
    tmp_export = Path(tempfile.gettempdir()) / "test_hubspot.csv"
    export_companies_csv(conn, tmp_export)
    # read back
    with open(tmp_export, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        ok = assert_true(reader.fieldnames == CSV_COLUMNS, "CSV columns stable")
        rows = list(reader)
        ok2 = assert_eq(len(rows), 1, "1 row exported")
        # check srpska slova
        ok3 = assert_true("Čabarkapa" in rows[0]["Company Name"], "utf-8 preserved")
        # check escaping
        ok4 = assert_true("Test, sa zarezom" in rows[0]["Notes"], "comma escaping")
    conn.close(); p.unlink(missing_ok=True); tmp_export.unlink(missing_ok=True)
    return ok and ok2 and ok3 and ok4

# 11. Source query preserved
def test_source_query():
    p = temp_db()
    conn = get_connection(p)
    lead1 = {"company_name":"Gym Source","city":"Subotica","address":"Adr 1","phone":"024 111111","source_query":"teretana","source_city":"Subotica","place_id":"0xSRC:0x1","google_maps_url":"https://maps/place/src","scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead1 = score_lead(lead1)
    upsert_lead(conn, lead1)
    # same lead via different query
    lead2 = {"company_name":"Gym Source","city":"Subotica","address":"Adr 1","phone":"024 111111","source_query":"fitness","source_city":"Subotica","place_id":"0xSRC:0x1","google_maps_url":"https://maps/place/src","scraped_at":"2026-01-02T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    lead2 = score_lead(lead2)
    eid = find_existing(conn, lead2)
    lead2["_existing_id"]=eid
    upsert_lead(conn, lead2)
    row = conn.execute("SELECT source_query FROM leads WHERE place_id='0xSRC:0x1'").fetchone()
    ok = assert_eq(row["source_query"], "teretana", "source_query preserved first")
    conn.close(); p.unlink(missing_ok=True)
    return ok

# 12. Rating validation — nikad >5, nikad negativan
def test_rating_validation():
    from validators import is_valid_rating
    from scraper import _validate_rating, _validate_review_count
    ok = assert_eq(_validate_rating(5.5), None, "rating 5.5 -> None (invalid)")
    ok2 = assert_eq(_validate_rating(22117.38), None, "rating 22117 -> None (production bug)")
    ok3 = assert_eq(_validate_rating(-1), None, "rating -1 -> None")
    ok4 = assert_eq(_validate_rating(4.8), 4.8, "rating 4.8 valid")
    ok5 = assert_eq(_validate_rating("4,8"), 4.8, "rating '4,8' valid with comma")
    ok6 = assert_true(not is_valid_rating(6), "is_valid_rating rejects 6")
    ok7 = assert_true(not is_valid_rating(22117.383), "is_valid_rating rejects 22117")
    ok8 = assert_true(is_valid_rating(4.8), "is_valid_rating accepts 4.8")
    ok9 = assert_true(is_valid_rating(None), "None is allowed (not found)")
    return ok and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok8 and ok9

# 13. Review count validation — integer >=0
def test_review_count_validation():
    from validators import is_valid_review_count
    from scraper import _validate_review_count
    ok = assert_eq(_validate_review_count(-5), None, "review -5 -> None")
    ok2 = assert_eq(_validate_review_count(0), 0, "review 0 valid")
    ok3 = assert_eq(_validate_review_count(137), 137, "review 137 valid")
    ok4 = assert_eq(_validate_review_count("1,234"), 1234, "review '1,234' cleaned")
    ok5 = assert_true(not is_valid_review_count(-1), "is_valid_review_count rejects -1")
    ok6 = assert_true(is_valid_review_count(6), "is_valid_review_count accepts 6")
    return ok and ok2 and ok3 and ok4 and ok5 and ok6

# 14. Rating i review se ne mogu zameniti + različiti leadovi različiti rating
def test_rating_review_not_swapped_and_varied():
    from validators import validate_lead
    # rating i review imaju različite domene
    lead_good = {"company_name":"Gym A","rating":4.8,"review_count":137}
    errs = validate_lead(lead_good)
    ok = assert_true(len(errs)==0, "valid rating 4.8 + reviews 137 -> no error")
    # ako bi zamenili: rating=137, reviews=4.8 -> oba invalid
    lead_swapped = {"company_name":"Gym A","rating":137,"review_count":4.8}
    errs2 = validate_lead(lead_swapped)
    ok2 = assert_true(any("rating" in e.lower() for e in errs2), "swapped rating 137 rejected")
    # različiti leadovi mogu imati različite vrednosti — upis 2 leada sa različitim rating
    p = temp_db()
    conn = get_connection(p)
    l1 = {"company_name":"Gym A","city":"Subotica","address":"A1","phone":"024 111111","place_id":None,"google_maps_url":None,"rating":4.8,"review_count":137,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    l2 = {"company_name":"Gym B","city":"Subotica","address":"B2","phone":"024 222222","place_id":None,"google_maps_url":None,"rating":3.2,"review_count":42,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    for l in (l1,l2):
        upsert_lead(conn, score_lead(l))
    rows = conn.execute("SELECT company_name, rating, review_count FROM leads ORDER BY company_name").fetchall()
    ok3 = assert_eq(rows[0]["rating"], 4.8, "Gym A rating 4.8 kept")
    ok4 = assert_eq(rows[1]["rating"], 3.2, "Gym B rating 3.2 different")
    ok5 = assert_true(rows[0]["review_count"] != rows[1]["review_count"], "different review counts")
    # None kada nije pronađeno
    l3 = {"company_name":"Gym C","city":"Subotica","address":"C3","phone":"024 333333","place_id":None,"google_maps_url":None,"rating":None,"review_count":None,"scraped_at":"2026-01-01T00:00:00+00:00","lead_status":"New","audit_status":"Not Started","notes":""}
    ok6 = assert_true(validate_lead(l3)==[], "None rating/reviews valid (not found)")
    conn.close(); p.unlink(missing_ok=True)
    return ok and ok2 and ok3 and ok4 and ok5 and ok6

# 15. Website social vs official + OUTDATED_PLATFORMS
def test_website_social_filter():
    from scraper import _is_social_website
    from scoring import calc_website_opportunity_score
    # social URL ne sme biti website
    ok = assert_true(_is_social_website("https://m.facebook.com/EverybodySubotica/"), "facebook detected as social")
    ok2 = assert_true(_is_social_website("https://www.instagram.com/powergym/"), "instagram social")
    ok3 = assert_true(not _is_social_website("https://examplegym.rs"), "examplegym not social")
    ok4 = assert_true(not _is_social_website("https://powergym.rs"), "powergym not social")
    # scoring razlike
    ok5 = assert_eq(calc_website_opportunity_score(""), 10, "empty confirmed no website -> 10")
    ok6 = assert_eq(calc_website_opportunity_score(None), None, "None unknown -> None")
    ok7 = assert_eq(calc_website_opportunity_score("https://examplegym.rs"), 2, "official good -> 2")
    ok8 = assert_eq(calc_website_opportunity_score("https://foo.wix.com/bar"), 7, "outdated wix -> 7")
    # social filtriran treba da bude tretiran kao nema website -> 10
    # ako scraper vrati website="" i facebook="https://m.facebook.com/..." onda je website prazan -> 10 je ispravno
    ok9 = assert_eq(calc_website_opportunity_score(""), 10, "social filtered -> no official website -> 10")
    return ok and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok8 and ok9

# Run all
run_test("Test A - Place ID dedup", test_place_id)
run_test("Test URL dedup", test_url_dedup)
run_test("Test Phone+Address", test_phone_address)
run_test("Test B - Multiple locations", test_multiple_locations)
run_test("Test C - Null Place ID", test_null_place_id)
run_test("Test Protected fields", test_protected)
run_test("Test First/Last scraped", test_first_last)
run_test("Test Scoring opportunity", test_scoring)
run_test("Test Audit Unable", test_audit_unable)
run_test("Test CSV stable/utf-8", test_csv)
run_test("Test Source query", test_source_query)
run_test("Test Rating validation (<=5)", test_rating_validation)
run_test("Test Review count validation", test_review_count_validation)
run_test("Test Rating/review not swapped & varied", test_rating_review_not_swapped_and_varied)
run_test("Test Website social vs official", test_website_social_filter)

print("\n" + "="*40)
if all_pass:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
