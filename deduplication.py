"""
deduplication.py — 5 nivoa deduplikacije prema specifikaciji

Prioritet:
1. place_id (Google Place ID)
2. Google Maps URL (normalizovan)
3. Phone (normalizovan)
4. Company Name + Address (normalizovano)
5. Fuzzy Company Name + City/Address fallback

Ako isti biznis ima različite fizičke adrese → tretirati kao odvojene jedinice.
"""

import re
import sqlite3
from difflib import SequenceMatcher

from validators import normalize_name, normalize_phone, normalize_url, normalize_address


def extract_place_id(gmaps_url: str) -> str | None:
    """
    Pokušava da izvuče Place ID iz Google Maps URL-a.
    Primeri URL-ova:
    - https://www.google.com/maps/place/Naziv/@45.25,19.84,17z/data=!4m...!1s0x1234:0xabcd...
    - https://www.google.com/maps/place/Naziv/data=!4m...!1sChIJ...
    Place ID je hex posle !1s ili ChIJ...
    Ako ne uspe, vraća None.
    """
    if not gmaps_url:
        return None
    # Pattern 1: !1s0x...:0x...  (ftid)
    m = re.search(r"!1s(0x[a-fA-F0-9]+:0x[a-fA-F0-9]+)", gmaps_url)
    if m:
        return m.group(1)
    # Pattern 2: ChIJ... (Place ID base64-ish, 27 chars)
    m = re.search(r"ChIJ[A-Za-z0-9_-]{20,}", gmaps_url)
    if m:
        return m.group(0)
    # Pattern 3: 0x... hex id u URL-u nakon /place/
    # Fallback: koristi ceo URL kao identifikator ako je stabilan
    return None


def find_existing(conn: sqlite3.Connection, lead: dict) -> int | None:
    """
    Vrati id postojećeg lead-a u bazi ili None ako ne postoji.
    Implementira 5 nivoa prioriteta.

    lead očekuje ključeve: place_id, google_maps_url, phone, company_name, address, city
    """
    # ── Nivo 1: place_id ──
    place_id = lead.get("place_id")
    if place_id:
        cur = conn.execute("SELECT id FROM leads WHERE place_id = ?", (place_id,))
        row = cur.fetchone()
        if row:
            return row["id"]

    # ── Nivo 2: google_maps_url (normalizovan) ──
    gurl = lead.get("google_maps_url")
    if gurl:
        norm_url = normalize_url(gurl)
        # direktno poklapanje
        cur = conn.execute("SELECT id, google_maps_url FROM leads WHERE google_maps_url IS NOT NULL")
        for row in cur.fetchall():
            if normalize_url(row["google_maps_url"]) == norm_url:
                return row["id"]

    # ── Nivo 3: phone (normalizovan) ──
    phone = lead.get("phone")
    if phone:
        norm_phone = normalize_phone(phone)
        if norm_phone and len(re.sub(r"\D", "", norm_phone)) >= 8:
            cur = conn.execute("SELECT id, phone FROM leads WHERE phone IS NOT NULL AND phone != ''")
            for row in cur.fetchall():
                if normalize_phone(row["phone"]) == norm_phone:
                    # Dodatna provera: ako je adresa različita, možda su 2 lokacije istog lanca
                    # Ali telefon je jak identifikator — ako je isti telefon, verovatno ista firma
                    # Ipak, ako lead ima različitu adresu i isto ime lanca sa više lokacija,
                    # nećemo spajati — proveravamo adresu
                    existing_addr = ""
                    cur2 = conn.execute("SELECT address FROM leads WHERE id = ?", (row["id"],))
                    r2 = cur2.fetchone()
                    if r2:
                        existing_addr = r2["address"] or ""
                    # Ako oba imaju adresu i adrese su različite (>30% razlike), preskoči phone-match
                    if lead.get("address") and existing_addr:
                        if normalize_address(lead["address"]) != normalize_address(existing_addr):
                            # Različite adrese → moguće 2 lokacije, ne spajaj po telefonu
                            # Ali ako je adresa samo malo drugačija (npr. skraćena), i dalje spoji
                            # Koristimo fuzzy: ako je sličnost < 0.7, tretiraj kao različitu lokaciju
                            sim = SequenceMatcher(None, normalize_address(lead["address"]), normalize_address(existing_addr)).ratio()
                            if sim < 0.7:
                                continue
                    return row["id"]

    # ── Nivo 4: Company Name + Address (egzaktno, normalizovano) ──
    name = lead.get("company_name")
    address = lead.get("address")
    if name and address:
        norm_name = normalize_name(name)
        norm_addr = normalize_address(address)
        cur = conn.execute("SELECT id, company_name, address FROM leads WHERE company_name IS NOT NULL AND address IS NOT NULL")
        for row in cur.fetchall():
            if normalize_name(row["company_name"]) == norm_name and normalize_address(row["address"]) == norm_addr:
                return row["id"]

    # ── Nivo 5: Fuzzy Company Name + City/Address ──
    if name:
        norm_name = normalize_name(name)
        city = (lead.get("city") or "").strip().lower()
        addr = normalize_address(address or "")
        cur = conn.execute("SELECT id, company_name, city, address FROM leads")
        best_match = None
        best_ratio = 0
        for row in cur.fetchall():
            existing_name = normalize_name(row["company_name"] or "")
            if not existing_name:
                continue
            # Ime mora biti slično > 0.85
            ratio = SequenceMatcher(None, norm_name, existing_name).ratio()
            if ratio < 0.85:
                continue
            # Dodatno: grad mora biti isti ILI adresa slična
            existing_city = (row["city"] or "").strip().lower()
            existing_addr = normalize_address(row["address"] or "")

            city_match = city and existing_city and city == existing_city
            addr_sim = SequenceMatcher(None, addr, existing_addr).ratio() if addr and existing_addr else 0

            # Ako oba imaju adresu i adrese su pouzdano razlicite (<0.70), ne spajaj — razlicite lokacije
            if addr and existing_addr and addr_sim < 0.70:
                continue

            # Ako je grad isti i ime slično → dovoljno (uz proveru adrese gore)
            # Ako grad nije isti ali adresa je vrlo slična → takođe
            if city_match or addr_sim > 0.8:
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = row["id"]
            # Ako nema grada/adrese za poređenje, zahtevaj veći threshold za ime (0.92)
            elif not city and not addr and ratio >= 0.92:
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = row["id"]

        if best_match:
            return best_match

    return None


def deduplicate_batch(leads: list[dict]) -> tuple[list[dict], int]:
    """
    Deduplikacija unutar batch-a pre upisa u bazu (bez DB).
    Koristi iste normalizacije.
    Vraća (unique_leads, duplicates_removed_count)
    """
    seen_place_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_phones: set[str] = set()
    seen_name_addr: set[tuple[str, str]] = set()
    unique: list[dict] = []
    dup_count = 0

    for lead in leads:
        is_dup = False

        pid = lead.get("place_id")
        if pid and pid in seen_place_ids:
            is_dup = True
        elif pid:
            seen_place_ids.add(pid)

        if not is_dup:
            gurl = normalize_url(lead.get("google_maps_url") or "")
            if gurl and gurl in seen_urls:
                is_dup = True
            elif gurl:
                seen_urls.add(gurl)

        if not is_dup:
            ph = normalize_phone(lead.get("phone") or "")
            if ph and len(re.sub(r"\D", "", ph)) >= 8 and ph in seen_phones:
                is_dup = True
            elif ph and len(re.sub(r"\D", "", ph)) >= 8:
                seen_phones.add(ph)

        if not is_dup:
            key = (normalize_name(lead.get("company_name") or ""), normalize_address(lead.get("address") or ""))
            if key[0] and key[1] and key in seen_name_addr:
                is_dup = True
            elif key[0] and key[1]:
                seen_name_addr.add(key)

        if is_dup:
            dup_count += 1
        else:
            unique.append(lead)

    return unique, dup_count
