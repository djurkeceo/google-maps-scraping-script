"""
prioritization.py — Lead Prioritization Engine
Modularni sloj preko scoring.py / website_audit.py
Ne duplira opportunity logiku, samo je kombinuje sa business_strength.

Svi pragovi su u config.py — nema hard-code u funkcijama osim fallback-a.
"""

import json
from typing import Tuple

from config import (
    BUSINESS_STRENGTH_CONFIG,
    PRIORITY_CONFIG,
    SERVICE_RECOMMENDATION_CONFIG,
)


# ── Business Strength 0-10 ──

def calculate_business_strength(lead: dict) -> int | None:
    """
    Koliko je jak market signal (potražnja).
    Koristi rating, review_count, social presence.
    Diminishing returns za reviews: 10→100 mnogo veći skok nego 1000→1100.
    Ako nema validnih podataka, vraća None (ne izmišlja).
    """
    rating = lead.get("rating")
    reviews = lead.get("review_count")
    has_social = bool((lead.get("instagram") or "").strip() or (lead.get("facebook") or "").strip())

    # ako ni rating ni reviews nema, ne možemo proceniti — ali ako ima website/social, daj minimalno?
    # strogo: ako oba None, vrati None
    if rating is None and reviews is None:
        # ako ima bar social, vrati nizak score umesto None da ne bude potpuno nepoznato?
        # ipak, po spec: ne izmišljaj — ako nema rating/reviews, business_strength = None
        return None

    # rating_score 0-4
    rating_score = 0
    if rating is not None:
        try:
            r = float(rating)
            if 0 <= r <= 5:
                for thresh, score in BUSINESS_STRENGTH_CONFIG["rating_scores"]:
                    if r >= thresh:
                        rating_score = score
                        break
            else:
                rating_score = 0
        except (ValueError, TypeError):
            rating_score = 0
    else:
        rating_score = 0

    # review_score 0-10 sa diminishing returns
    review_score = 0
    if reviews is not None:
        try:
            rv = int(reviews)
            if rv < 0:
                rv = 0
            for upper, score in BUSINESS_STRENGTH_CONFIG["review_scores"]:
                if rv <= upper:
                    review_score = score
                    break
        except (ValueError, TypeError):
            review_score = 0
    else:
        review_score = 0

    # ako je jedini signal rating ili reviews =0, a drugi None, ne kažnjavaj previše
    # ako je reviews None ali rating visok, i dalje daj visok business
    # težine iz configa
    w = BUSINESS_STRENGTH_CONFIG["weights"]
    social_bonus = BUSINESS_STRENGTH_CONFIG["social_bonus"] if has_social else 0

    # ako je reviews None, smanji težinu reviews na 0 i preraspodeli?
    # jednostavno: ako je reviews None, koristi samo rating + social
    if reviews is None and rating is not None:
        # rating 60% + social 5% -> normalizuj na 65% -> skaliraj
        raw = rating_score * 0.6 + social_bonus
        # rating_score je 0-4, treba ga skalirati na 0-10 pre množenja?
        # rating_score 4 => max, review_score 10 => max
        # konvertuj rating_score (0-4) na 0-10 skalu: *2.5
        rating_scaled = rating_score * 2.5  # 0-10
        raw = rating_scaled * w["rating"] + social_bonus * w["social"] * 10  # social_bonus 1 => 0.05*10=0.5
        # za slučaj samo rating, normalizuj deljenjem sa sumom težina koje imamo
        total_w = w["rating"] + w["social"]
        if total_w > 0:
            raw = raw / total_w
        # clamp
        result = int(round(min(10, max(0, raw))))
        # ako je rating None i reviews None već smo vratili None, ovde rating postoji
        return result
    if rating is None and reviews is not None:
        # samo reviews + social
        raw = review_score * w["reviews"] + social_bonus * w["social"] * 10
        total_w = w["reviews"] + w["social"]
        if total_w > 0:
            raw = raw / total_w
        return int(round(min(10, max(0, raw))))

    # oba postoje
    rating_scaled = rating_score * 2.5  # 0-10
    raw = rating_scaled * w["rating"] + review_score * w["reviews"] + social_bonus * w["social"] * 10
    # w zbir je 1.0, pa je raw već 0-10
    return int(round(min(10, max(0, raw))))


# ── Priority Score 0-100 ──

def calculate_priority(business_strength: int | None, website_opportunity: int | None, seo_opportunity: int | None, conversion_opportunity: int | None) -> int | None:
    """
    Glavni score za sortiranje.
    STRONG BUSINESS + STRONG OPPORTUNITY = visok.
    Koristi max opportunity (najveći problem) — ne prosek svih, jer jedan veliki problem je dovoljan.
    Ako nema business i nema opportunity, vrati None.
    """
    # opportunity je max od tri
    opps = [o for o in [website_opportunity, seo_opportunity, conversion_opportunity] if o is not None]
    max_opp = max(opps) if opps else None

    if business_strength is None and max_opp is None:
        return None
    # ako jedno fali, koristi drugo
    if business_strength is None:
        # samo opportunity — smanji za 30% jer nema dokaza potražnje
        return int(round(max_opp * 7))  # max 70
    if max_opp is None:
        # samo business, nema problema — nizak priority
        return int(round(business_strength * 3))  # max 30

    w = PRIORITY_CONFIG["weights"]
    # business 0-10, opp 0-10 -> priority 0-100
    # formula: (business*0.55 + opp*0.45)*10
    score = (business_strength * w["business"] + max_opp * w["opportunity"]) * 10
    return int(round(min(100, max(0, score))))


def calculate_priority_level(priority_score: int | None) -> str:
    if priority_score is None:
        return "LOW"
    thresh = PRIORITY_CONFIG["thresholds"]
    if priority_score >= thresh["HIGH"]:
        return "HIGH"
    if priority_score >= thresh["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


# ── Service Recommendation ──

def recommend_services(lead: dict, business_strength: int | None) -> list[str]:
    """
    Vraća listu Zeltro usluga. Nikad ne izmišlja problem.
    Pragovi iz SERVICE_RECOMMENDATION_CONFIG.
    Ako je business slab (< min_business), vrati prazno (Low Priority).
    """
    cfg = SERVICE_RECOMMENDATION_CONFIG
    # ako je business preslab, ne preporučuj ništa — nema smisla prodavati jaku uslugu slabom biznisu
    if business_strength is not None and business_strength < cfg["min_business_for_service"]:
        return []

    services = []
    website = (lead.get("website") or "").strip()
    has_website = bool(website)

    # website opportunity
    w_opp = lead.get("website_opportunity_score")
    if w_opp is None:
        w_opp = lead.get("website_score")

    if not has_website and w_opp is not None and w_opp >= cfg["website_threshold"]:
        services.append("Website Development")
        # ako nema website, local SEO je skoro uvek relevantan
        services.append("Local SEO")
        # ukloni duplikat ako već dodaje
        # za Local SEO ne treba poseban audit, dovoljan je nedostatak website-a + jak business
        # ali dodaj samo ako business nije LOW
    elif has_website and w_opp is not None and w_opp >= cfg["website_threshold"]:
        # ima website ali je outdated
        services.append("Website Redesign")

    # SEO
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is None:
        seo_opp = lead.get("seo_score")
    if seo_opp is not None and seo_opp >= cfg["seo_threshold"]:
        # Local SEO je uvek korisan za lokalni biznis, ali dodaj SEO kao glavni
        if "Website Development" not in services:  # ako već ima website problem, SEO je sekundaran
            services.append("SEO")
            if "Local SEO" not in services:
                services.append("Local SEO")
        else:
            # ako već ima Website Development, SEO/Local je bonus
            if "Local SEO" not in services:
                services.append("Local SEO")

    # Conversion
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is None:
        conv_opp = lead.get("conversion_score")
    if conv_opp is not None and conv_opp >= cfg["conversion_threshold"]:
        services.append("Conversion Optimization")

    # Performance
    rt = lead.get("response_time_ms")
    http = lead.get("http_status")
    if rt is not None and isinstance(rt, int) and rt >= cfg["performance_threshold_response_ms"]:
        services.append("Performance Optimization")
    if http is not None and isinstance(http, int) and http >= 400:
        if "Website Development" not in services and "Website Redesign" not in services:
            services.append("Website Maintenance")

    # ukloni duplikate, sačuvaj redosled
    seen = set()
    unique = []
    for s in services:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    # ako je business jak ali nema visok opportunity, ne izmišljaj
    if not unique:
        return []

    return unique


def classify_lead(business_strength: int | None, recommended_services: list[str], priority_level: str) -> str:
    """
    lead_type klasifikacija.
    """
    if not recommended_services:
        if priority_level == "LOW":
            return "Low Priority"
        return "Manual Review"

    # ako ima 2+ različite kategorije
    # Website vs SEO vs Conversion su različite
    service_types = set()
    for s in recommended_services:
        if "Website" in s:
            service_types.add("Website")
        elif "SEO" in s:
            service_types.add("SEO")
        elif "Conversion" in s:
            service_types.add("Conversion")
        elif "Performance" in s:
            service_types.add("Performance")
        else:
            service_types.add(s)

    if len(service_types) >= 2:
        return "Multi-Service Opportunity"

    rs = recommended_services[0]
    if "Website Development" in rs or "Website Redesign" in rs:
        return "Website Opportunity"
    if "SEO" in rs or "Local SEO" in rs:
        return "Local SEO Opportunity" if "Local SEO" in rs else "SEO Opportunity"
    if "Conversion" in rs:
        return "Conversion Opportunity"
    if "Performance" in rs:
        return "Performance Opportunity"

    return "Manual Review"


# ── Reason / Sales Angle / Confidence ──

def generate_lead_reason(lead: dict, business_strength: int | None, priority_score: int | None) -> str:
    """
    Kratak razlog zašto je lead interesantan. Koristan pre cold call-a.
    Nikad generički.
    """
    name = (lead.get("company_name") or "").strip()
    rating = lead.get("rating")
    reviews = lead.get("review_count")
    website = (lead.get("website") or "").strip()
    has_website = bool(website)

    parts = []

    # business signal
    if rating is not None and reviews is not None:
        parts.append(f"{rating} rating, {reviews} reviews")
    elif rating is not None:
        parts.append(f"{rating} rating")
    elif reviews is not None:
        parts.append(f"{reviews} reviews")
    elif business_strength is not None and business_strength >= 6:
        parts.append("strong local presence")

    business_phrase = ""
    if parts:
        business_phrase = f"Strong Google presence ({', '.join(parts)})"
    elif business_strength is not None and business_strength >= 7:
        business_phrase = "Strong local business"
    elif business_strength is not None and business_strength <= 3:
        business_phrase = "Limited market signal"

    # opportunity
    opp_phrases = []
    w_opp = lead.get("website_opportunity_score")
    if w_opp is None:
        w_opp = lead.get("website_score")
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is None:
        seo_opp = lead.get("seo_score")
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is None:
        conv_opp = lead.get("conversion_score")

    if not has_website and w_opp is not None and w_opp >= 7:
        opp_phrases.append("no official website")
    elif has_website and w_opp is not None and w_opp >= 7:
        opp_phrases.append("outdated website")

    if seo_opp is not None and seo_opp >= 6:
        opp_phrases.append("significant SEO opportunity" if seo_opp >= 7 else "SEO opportunity")
    if conv_opp is not None and conv_opp >= 6:
        opp_phrases.append("significant conversion weaknesses" if conv_opp >= 7 else "conversion opportunity")

    if not opp_phrases:
        if has_website and w_opp is not None and w_opp <= 3 and seo_opp is not None and seo_opp <= 3 and conv_opp is not None and conv_opp <= 3:
            opp_phrases.append("no obvious digital gap — manual review needed")
        elif not has_website:
            opp_phrases.append("no official website")
        else:
            opp_phrases.append("limited digital opportunity")

    # kombinuj
    if business_phrase and opp_phrases:
        if "Limited market signal" in business_phrase:
            return f"{business_phrase} ({', '.join(parts) if parts else 'few reviews'}) but {opp_phrases[0]}."
        # za jak business
        opp_str = " but ".join(opp_phrases[:2]) if len(opp_phrases) > 1 else opp_phrases[0]
        if "Strong Google presence" in business_phrase:
            return f"{business_phrase} but {opp_str}."
        else:
            return f"{business_phrase} with {opp_str}."
    elif opp_phrases:
        return f"{opp_phrases[0].capitalize()}."
    elif business_phrase:
        return f"{business_phrase}."
    else:
        return "Manual review — insufficient data."


def generate_sales_angle(recommended_services: list[str], lead_type: str) -> str:
    """
    Smernica za razgovor, ne skripta.
    """
    if not recommended_services:
        return "Manual review — no clear service angle without further research."

    # prioritizuj prvu uslugu
    primary = recommended_services[0]
    if "Website Development" in primary:
        return "Focus on converting existing Google/Instagram traffic into new members via a proper website."
    if "Website Redesign" in primary:
        return "Focus on modernizing the existing site to improve mobile experience and trust."
    if "Local SEO" in primary or lead_type in ("Local SEO Opportunity", "SEO Opportunity"):
        return "Focus on increasing local search visibility for people searching for gyms in the area."
    if "Conversion" in primary:
        return "Focus on turning existing website visitors into trial bookings."
    if "Performance" in primary:
        return "Focus on faster mobile experience and reduced friction."
    if "SEO" in primary:
        return "Focus on organic visibility and content for local searches."
    return f"Focus on {primary.lower()} as the primary value driver."


def calculate_confidence(lead: dict, business_strength: int | None) -> str:
    """
    High/Medium/Low — koliko je pouzdana procena.
    High: rating + reviews + (website ili bar jedan audit sa Completed)
    Medium: bar 2 od 3, Low: manje ili website unknown.
    Nikad ne tvrdi da nema website ako nije pouzdano.
    """
    has_rating = lead.get("rating") is not None
    has_reviews = lead.get("review_count") is not None
    has_website = bool((lead.get("website") or "").strip())
    has_no_website = (lead.get("website") == "")  # potvrdjeno nema
    has_website_unknown = lead.get("website") is None
    audit_status = (lead.get("automated_audit_status") or "").strip()

    # ako je website None (unknown), automatski Low/Medium
    if has_website_unknown:
        return "Low"

    # broji pouzdane signale
    signals = 0
    if has_rating:
        signals += 1
    if has_reviews:
        signals += 1
    if has_no_website or has_website:
        # ako imamo potvrdjeno stanje website-a, to je signal
        signals += 1
    if audit_status == "Completed":
        signals += 1

    if signals >= 3 and has_rating and has_reviews:
        return "High"
    if signals >= 2:
        return "Medium"
    return "Low"


def prioritize_lead(lead: dict) -> dict:
    """
    Glavna funkcija — prima lead dict (sa scoring i audit poljima), vraća isti dict dopunjen sa:
      business_strength_score, priority_score, priority, lead_type, recommended_services (JSON string), lead_reason, sales_angle, prioritization_confidence, prioritization_updated_at
    """
    from datetime import datetime, timezone
    import json

    # business strength
    bs = calculate_business_strength(lead)
    lead["business_strength_score"] = bs

    # opportunity već postoji u lead-u (scoring.py)
    w_opp = lead.get("website_opportunity_score")
    if w_opp is None:
        w_opp = lead.get("website_score")
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is None:
        seo_opp = lead.get("seo_score")
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is None:
        conv_opp = lead.get("conversion_score")

    priority = calculate_priority(bs, w_opp, seo_opp, conv_opp)
    level = calculate_priority_level(priority)

    services = recommend_services(lead, bs)
    lead_type = classify_lead(bs, services, level)
    reason = generate_lead_reason(lead, bs, priority)
    angle = generate_sales_angle(services, lead_type)
    confidence = calculate_confidence(lead, bs)

    lead["priority_score"] = priority
    lead["priority"] = level
    lead["lead_type"] = lead_type
    # recommended_services čuvamo kao JSON string u bazi, a u CSV kao "A, B"
    lead["recommended_services"] = json.dumps(services, ensure_ascii=False)
    lead["lead_reason"] = reason
    lead["sales_angle"] = angle
    lead["prioritization_confidence"] = confidence
    lead["prioritization_updated_at"] = datetime.now(timezone.utc).isoformat()

    return lead
