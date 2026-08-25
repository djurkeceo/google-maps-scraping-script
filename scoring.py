"""
scoring.py — modularan lead scoring (opportunity model)

Princip: 0 = nema očiglednog problema (mala prilika), 10 = velika prilika / veliki problem
Ako nema dovoljno podataka za pouzdanu procenu, vraća None ("Not Evaluated" u CSV-u).

Svi skorovi su opportunity skorovi. Stari nazivi (website_score) ostaju kao aliasi za backward compat.
"""

import json
from config import OUTDATED_PLATFORMS, SCORING_NOT_EVALUATED

# ── Website Opportunity Score ──

def calc_website_score(website: str) -> int | None:
    """Alias za backward compat — poziva opportunity verziju."""
    return calc_website_opportunity_score(website)


def calc_website_opportunity_score(website: str | None) -> int | None:
    """
    0 = ima dobar sajt (manja prilika)
    10 = pouzdano nema official website (najbolji lead)
    7 = zastareo sajt (srednja prilika)
    None = nije moguće pouzdano utvrditi (unknown) — ne nagađaj
    """
    # website is None => unknown/did not check => Not Evaluated
    if website is None:
        return None
    # website == "" => potvrdjeno nema official website (proverili smo authority i nije social)
    if isinstance(website, str) and website.strip() == "":
        return 10
    url_lower = website.lower()
    for platform in OUTDATED_PLATFORMS:
        if platform in url_lower:
            return 7
    return 2


# ── SEO Opportunity Score ──

def calc_seo_score(website: str, has_website: bool = True) -> int | None:
    """Backward compat alias."""
    return calc_seo_opportunity_score(website, audit_data=None)


def calc_seo_opportunity_score(website: str | None, audit_data: dict | str | None = None) -> int | None:
    """
    SEO opportunity zahteva website + audit.
    - website is None -> None (unknown)
    - website == "" (nema sajta) -> None (N/A, ne možemo auditirati on-page SEO bez sajta)
    - sa sajtom ali bez audita -> None (Not Evaluated)
    - sa auditom: svaka missing SEO stavka povecava skor
    Za Local SEO bez website-a koristi calc_local_seo_opportunity_score.
    """
    if website is None:
        return None
    if isinstance(website, str) and website.strip() == "":
        return None

    # pokusaj da parsiras audit_data ako je JSON string
    if isinstance(audit_data, str):
        try:
            audit_data = json.loads(audit_data)
        except Exception:
            audit_data = None

    if not audit_data or not isinstance(audit_data, dict):
        # nema audit podataka — ne možemo pouzdano proceniti
        return None

    # ako je audit failovao, ne procenjuj
    if audit_data.get("error") or audit_data.get("http_status") is None or audit_data.get("http_status", 200) >= 400:
        return None

    # SEO signali — svaki missing = opportunity
    score = 0
    # weight distribucija do max 10
    if not audit_data.get("title"):
        score += 4  # najvažnije
    if not audit_data.get("meta_description"):
        score += 3
    if not audit_data.get("h1"):
        score += 2
    if not audit_data.get("viewport"):
        score += 1  # mobile signal
    # canonical/robots su manji signali, ali ako fale nešto
    # ne dodajemo više od 10
    return min(score, 10)


# ── Conversion Opportunity Score ──

def calc_conversion_score(website: str) -> int | None:
    """Backward compat alias."""
    return calc_conversion_opportunity_score(website, audit_data=None)


def calc_conversion_opportunity_score(website: str | None, audit_data: dict | str | None = None) -> int | None:
    """
    Conversion opportunity — samo ako postoji official website.
    - website is None -> None
    - website == "" (nema sajta) -> None (N/A, nema sta da se konvertuje)
    - sa sajtom bez audita -> None
    - sa auditom: proveri tel, form, booking, CTA, offer
    """
    if website is None:
        return None
    if isinstance(website, str) and website.strip() == "":
        return None

    if isinstance(audit_data, str):
        try:
            audit_data = json.loads(audit_data)
        except Exception:
            audit_data = None

    if not audit_data or not isinstance(audit_data, dict):
        return None

    if audit_data.get("error") or audit_data.get("http_status") is None or audit_data.get("http_status", 200) >= 400:
        return None

    score = 0
    if not audit_data.get("has_tel_link"):
        score += 2
    if not audit_data.get("has_form"):
        score += 2
    if not audit_data.get("has_booking_link"):
        score += 2
    if not audit_data.get("has_cta"):
        score += 2
    if not audit_data.get("has_offer"):
        score += 1
    if not audit_data.get("has_maps_link"):
        score += 1
    return min(score, 10)


# ── Local SEO Opportunity (0-10) — može i bez website-a ──

def calc_local_seo_opportunity_score(lead: dict, website: str | None, audit_data: dict | str | None = None) -> int | None:
    """
    Local SEO gap — procena na osnovu Google signala + website-a.
    - website is None -> None (unknown)
    - bez website-a: jak business (reviews/rating) => visok local SEO opportunity (treba website za lokalnu vidljivost)
    - sa website-om: koristi SEO audit kao proxy, ako nema audita -> None
    """
    if website is None:
        return None
    # bez website-a: local SEO opportunity postoji, ali zavisi od business_strength
    if isinstance(website, str) and website.strip() == "":
        # proceni business_strength ako nije u lead-u
        bs = lead.get("business_strength_score")
        # ako nema bs, probaj iz rating/reviews grubo
        if bs is None:
            try:
                reviews = int(lead.get("review_count") or 0)
                rating = float(lead.get("rating") or 0)
                if reviews >= 100 and rating >= 4.5:
                    bs = 8
                elif reviews >= 50:
                    bs = 6
                elif reviews >= 20:
                    bs = 4
                else:
                    bs = 2
            except Exception:
                bs = 2
        if bs >= 6:
            return 8
        if bs >= 4:
            return 6
        return 4

    # sa website-om: koristi SEO audit ako postoji, inače None
    if isinstance(audit_data, str):
        try:
            audit_data = json.loads(audit_data)
        except Exception:
            audit_data = None
    if not audit_data or not isinstance(audit_data, dict):
        return None
    if audit_data.get("error") or audit_data.get("http_status") is None or audit_data.get("http_status", 200) >= 400:
        return None
    # za local SEO, isti signali kao SEO ali sa manjim pragovima
    score = 0
    if not audit_data.get("title"):
        score += 3
    if not audit_data.get("meta_description"):
        score += 2
    if not audit_data.get("h1"):
        score += 2
    # local specific: nema Google Maps linka na sajtu, nema adrese? za sada pojednostavljeno
    return min(score, 8)


# ── Performance Opportunity (0-10) — samo sa audit evidence ──

def calc_performance_opportunity_score(audit_data: dict | str | None = None) -> int | None:
    """
    Performance gap — samo ako postoji merenje.
    - bez audit_data -> None (ne pretpostavljaj)
    - response_time_ms >3000 => 8, >1500 => 4, inače 0
    - http >=400 => 6 (maintenance)
    """
    if isinstance(audit_data, str):
        try:
            audit_data = json.loads(audit_data)
        except Exception:
            audit_data = None
    if not audit_data or not isinstance(audit_data, dict):
        return None
    if audit_data.get("error"):
        return None
    rt = audit_data.get("response_time_ms")
    http = audit_data.get("http_status")
    if rt is None and http is None:
        return None
    score = 0
    if isinstance(rt, int):
        if rt >= 3000:
            score = 8
        elif rt >= 1500:
            score = 4
        else:
            score = 0
    if isinstance(http, int) and http >= 400:
        score = max(score, 6)
    # ako je brz i http ok, nema opportunity
    if score == 0:
        return 2  # mali performance gap, ali ne visok
    return min(score, 10)


# ── Business Opportunity (optional, minimal) ──

def calc_business_score(lead: dict) -> int | None:
    """
    Business attractiveness — koliko je biznis vredan kao lead nezavisno od sajta.
    Veći broj recenzija + visok rating + social presence => atraktivniji.
    Vraća 0-10 gde je veći = atraktivniji бизнис (nije direktno opportunity, ali pomaže prioritizaciji).
    Ako nema dovoljno podataka, vraća None.
    Ova komponenta je namerno konzervativna.
    """
    # Za sada ostavljamo None — treba definisati pravila sa korisnikom
    # Primer implementacije (zakomentarisano):
    # rating = lead.get("rating")
    # reviews = lead.get("review_count")
    # has_social = bool(lead.get("instagram") or lead.get("facebook"))
    # if rating is None and reviews is None:
    #     return None
    # score = 0
    # if rating and rating >= 4.5: score += 3
    # elif rating and rating >= 4.0: score += 1
    # if reviews and reviews >= 300: score += 4
    # elif reviews and reviews >= 100: score += 2
    # if has_social: score += 1
    # return min(score, 10)
    return None


# ── Ukupan Lead Score ──

def calc_lead_score(website_score: int | None, seo_score: int | None, conversion_score: int | None, business_score: int | None = None) -> int | None:
    """
    Ukupan lead score 0-10, ponderisano.
    Website opportunity ima veću težinu (x2) jer je najpouzdaniji.
    Business score se trenutno ignoriše (None) dok se ne definiše.
    """
    scores = []
    weights = []

    if website_score is not None:
        scores.append(website_score)
        weights.append(2)
    if seo_score is not None:
        scores.append(seo_score)
        weights.append(1)
    if conversion_score is not None:
        scores.append(conversion_score)
        weights.append(1)
    if business_score is not None:
        scores.append(business_score)
        weights.append(1)

    if not scores:
        return None

    weighted_sum = sum(s * w for s, w in zip(scores, weights))
    total_weight = sum(weights)
    return round(weighted_sum / total_weight)


def score_lead(lead: dict, audit_data: dict | str | None = None) -> dict:
    """
    Izračunaj sve skorove za lead i upiši ih u lead dict.
    Podrzava i stare i nove nazive.
    Ako je audit_data prosleđen, koristi ga za SEO/conversion.
    Ako lead već ima audit_data_json, koristi njega kao fallback.
    """
    website = lead.get("website", "")

    # ako audit_data nije prosleđen, probaj iz lead-a
    if audit_data is None:
        audit_data = lead.get("audit_data_json")

    ws = calc_website_opportunity_score(website)
    ss = calc_seo_opportunity_score(website, audit_data=audit_data)
    cs = calc_conversion_opportunity_score(website, audit_data=audit_data)
    # novi: local SEO i performance — strogo evidence-based, None ako nema website/audit
    local_seo = calc_local_seo_opportunity_score(lead, website, audit_data=audit_data)
    perf = calc_performance_opportunity_score(audit_data=audit_data)
    bs = calc_business_score(lead)

    # Upisi i nove i stare ključeve za kompatibilnost
    lead["website_opportunity_score"] = ws
    lead["website_score"] = ws

    lead["seo_opportunity_score"] = ss
    lead["seo_score"] = ss

    lead["conversion_opportunity_score"] = cs
    lead["conversion_score"] = cs

    lead["local_seo_opportunity_score"] = local_seo
    lead["performance_opportunity_score"] = perf

    lead["lead_score"] = calc_lead_score(ws, ss, cs, bs)

    return lead


def score_to_csv_value(score: int | None) -> str:
    """Konvertuj skor za CSV: None → prazno, int → string."""
    if score is None:
        return SCORING_NOT_EVALUATED
    return str(score)
