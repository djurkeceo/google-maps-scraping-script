"""
validators.py — validacija i normalizacija pre upisa u bazu / exporta
"""

import re
from urllib.parse import urlparse

# ── Normalizacija ──

def normalize_name(name: str) -> str:
    if not name:
        return ""
    # lowercase, trim, collapse spaces, ukloni suvišne znakove na krajevima
    n = name.strip().lower()
    n = re.sub(r"\s+", " ", n)
    # ukloni navodnike i sl. na krajevima
    n = n.strip("\"'ʼ`´")
    return n


def normalize_phone(phone: str) -> str:
    """Vraća normalizovan telefon za poređenje: samo cifre i + na početku."""
    if not phone:
        return ""
    # zadrži cifre i +
    cleaned = re.sub(r"[^\d+]", "", phone)
    # normalizuj +381 / 381 / 06x varijante — ostavi kako jeste ali bez razmaka
    # za SR: ako počinje sa 0, ostavi 0; ako počinje sa 381 bez +, dodaj +
    if cleaned.startswith("381") and not cleaned.startswith("+381"):
        cleaned = "+" + cleaned
    # ukloni dupli +
    cleaned = re.sub(r"^\++", "+", cleaned)
    return cleaned


def normalize_url(url: str) -> str:
    if not url:
        return ""
    u = url.strip().lower()
    # dodaj https:// ako fali
    if not u.startswith("http"):
        # instagram/facebook često dolaze bez sheme — preskoči
        if u.startswith("www.") or "." in u:
            u = "https://" + u
    # ukloni trailing /
    u = u.rstrip("/")
    # ukloni www.
    u = re.sub(r"^https://www\.", "https://", u)
    u = re.sub(r"^http://www\.", "http://", u)
    return u


def normalize_address(addr: str) -> str:
    if not addr:
        return ""
    a = addr.strip().lower()
    a = re.sub(r"\s+", " ", a)
    # ukloni interpunkciju koja ne menja lokaciju (zarez ostavljamo)
    a = re.sub(r"\s*,\s*", ", ", a)
    return a


# ── Validacija ──

def is_valid_phone(phone: str) -> bool:
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    # srpski brojevi: 8-15 cifara je validno internacionalno
    return 8 <= len(digits) <= 15


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url if url.startswith("http") else "https://" + url)
        return bool(parsed.netloc and "." in parsed.netloc)
    except Exception:
        return False


def is_valid_rating(rating) -> bool:
    if rating is None or rating == "":
        return True  # prazno je ok (nije greška)
    try:
        r = float(rating)
        return 0 <= r <= 5
    except (ValueError, TypeError):
        return False


def is_valid_review_count(count) -> bool:
    if count is None or count == "":
        return True
    try:
        c = int(count)
        return c >= 0
    except (ValueError, TypeError):
        return False


def validate_lead(lead: dict) -> list[str]:
    """
    Validira lead dict (snake_case ključevi kao u bazi).
    Vraća listu grešaka. Prazna lista = validan.
    """
    errors = []
    if not lead.get("company_name") or not str(lead["company_name"]).strip():
        errors.append("Company Name je obavezan")
    if lead.get("phone") and not is_valid_phone(lead["phone"]):
        errors.append(f"Nevalidan telefon: {lead['phone']}")
    if lead.get("website") and not is_valid_url(lead["website"]):
        errors.append(f"Nevalidan website URL: {lead['website']}")
    if lead.get("instagram") and not is_valid_url(lead["instagram"]):
        errors.append(f"Nevalidan Instagram URL: {lead['instagram']}")
    if lead.get("facebook") and not is_valid_url(lead["facebook"]):
        errors.append(f"Nevalidan Facebook URL: {lead['facebook']}")
    if not is_valid_rating(lead.get("rating")):
        errors.append(f"Nevalidan rating: {lead.get('rating')}")
    if not is_valid_review_count(lead.get("review_count")):
        errors.append(f"Nevalidan review_count: {lead.get('review_count')}")
    return errors


def clean_lead(lead: dict) -> dict:
    """Normalizuje polja za deduplikaciju/validaciju, ne menja originalne display vrednosti osim gde treba."""
    # Za poređenje koristimo normalizovane verzije, ali u bazu čuvamo čiste display vrednosti
    # Ova funkcija samo trim-uje i čisti phone/url
    if lead.get("phone"):
        # Sačuvaj display verziju ali očisti od suvišnih karaktera
        lead["phone"] = re.sub(r"[^\d+\s\-\(\)]", "", lead["phone"]).strip()
    if lead.get("website"):
        lead["website"] = lead["website"].strip()
    if lead.get("company_name"):
        lead["company_name"] = lead["company_name"].strip()
    if lead.get("address"):
        lead["address"] = lead["address"].strip()
    return lead
