"""
prioritization.py — Lead Prioritization Engine (kalibrisan)
Modularni sloj preko scoring.py / website_audit.py
Strogo evidence-based: svaka preporuka mora imati dokaz.
"""

import json
from typing import Tuple

from config import (
    BUSINESS_STRENGTH_CONFIG,
    PRIORITY_CONFIG,
    SERVICE_RECOMMENDATION_CONFIG,
)


# ── Business Strength 0-10 — kalibrisan, reviews dominantan, rating secondary ──

def calculate_business_strength(lead: dict) -> int | None:
    """
    Koliko je jak market signal. Review count ima diminishing returns.
    5.0/1 review NE SME biti HIGH. Koristi nove pragove iz config-a.
    """
    rating = lead.get("rating")
    reviews = lead.get("review_count")
    has_social = bool((lead.get("instagram") or "").strip() or (lead.get("facebook") or "").strip())

    if rating is None and reviews is None:
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
        except (ValueError, TypeError):
            rating_score = 0

    # review_score 0-10
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

    w = BUSINESS_STRENGTH_CONFIG["weights"]
    social_bonus = BUSINESS_STRENGTH_CONFIG["social_bonus"] if has_social else 0

    # Ako je jedno None, normalizuj težine
    if reviews is None and rating is not None:
        rating_scaled = rating_score * 2.5  # 0-10
        raw = rating_scaled * w["rating"] + social_bonus * w["social"] * 10
        total_w = w["rating"] + w["social"]
        raw = raw / total_w if total_w else 0
        return int(round(min(10, max(0, raw))))
    if rating is None and reviews is not None:
        raw = review_score * w["reviews"] + social_bonus * w["social"] * 10
        total_w = w["reviews"] + w["social"]
        raw = raw / total_w if total_w else 0
        return int(round(min(10, max(0, raw))))

    rating_scaled = rating_score * 2.5
    raw = rating_scaled * w["rating"] + review_score * w["reviews"] + social_bonus * w["social"] * 10
    return int(round(min(10, max(0, raw))))


# ── Priority Score 0-100 ──

def calculate_priority(business_strength: int | None, website_opportunity: int | None, seo_opportunity: int | None, conversion_opportunity: int | None, local_seo_opportunity: int | None = None, performance_opportunity: int | None = None) -> int | None:
    """
    Glavni score. Strong business + strong opportunity = HIGH.
    Koristi max opportunity (najveći problem) da jedan veliki problem ne bude razblažen.
    Uključuje i local_seo/performance ako postoje.
    """
    opps = [o for o in [website_opportunity, seo_opportunity, conversion_opportunity, local_seo_opportunity, performance_opportunity] if o is not None]
    max_opp = max(opps) if opps else None

    if business_strength is None and max_opp is None:
        return None
    if business_strength is None:
        return int(round(max_opp * 7))  # bez business dokaza, max 70
    if max_opp is None:
        return int(round(business_strength * 3))  # bez opportunity, max 30

    w = PRIORITY_CONFIG["weights"]
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


# ── Service Recommendation — strogo evidence-based ──

def recommend_services(lead: dict, business_strength: int | None) -> list[str]:
    """
    Svaka usluga mora imati evidence. Ne preporučuj ako business < min_business.
    Tabela evidence:
      Website Development -> website == "" (nema official)
      Website Redesign -> website exists + w_opp >=7
      SEO -> website exists + seo_opp >=6 (audit Completed)
      Local SEO -> local_seo_opp >=6 (može i bez website-a)
      Conversion -> website exists + conv_opp >=6 (audit Completed)
      Performance -> performance_opp >=6
    Multi-Service samo ako 2+ nezavisno potvrđene visoke prilike.
    """
    cfg = SERVICE_RECOMMENDATION_CONFIG
    if business_strength is not None and business_strength < cfg["min_business_for_service"]:
        return []

    services = []
    website = (lead.get("website") or "")
    # website is None = unknown, ne preporučuj Website Development ako je unknown
    has_website = bool(website.strip()) if isinstance(website, str) else False
    is_no_website = isinstance(website, str) and website.strip() == ""
    is_unknown = website is None

    # Website opportunity
    w_opp = lead.get("website_opportunity_score")
    if w_opp is None:
        w_opp = lead.get("website_score")
    local_opp = lead.get("local_seo_opportunity_score")
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is None:
        seo_opp = lead.get("seo_score")
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is None:
        conv_opp = lead.get("conversion_score")
    perf_opp = lead.get("performance_opportunity_score")

    # Website Development / Redesign — strogo
    if is_no_website and w_opp is not None and w_opp >= cfg["website_threshold"] and not is_unknown:
        services.append("Website Development")
    elif has_website and w_opp is not None and w_opp >= cfg["website_threshold"]:
        services.append("Website Redesign")

    # Local SEO — može i bez website-a, ali mora local_opp >= threshold
    if local_opp is not None and local_opp >= cfg.get("local_seo_threshold", cfg["seo_threshold"]):
        # Local SEO je validan i za no-website (jak business + no website)
        # ali ne dupliraj ako već ima Website Development + Local SEO je ok
        if "Local SEO" not in services:
            # za no-website, Local SEO je uvek relevantan ako je business jak
            if is_no_website:
                services.append("Local SEO")
            else:
                # sa website-om, Local SEO samo ako je audit pokazao priliku
                services.append("Local SEO")

    # SEO — samo ako website postoji i ima visok opportunity (audit evidence je sam score)
    if has_website and seo_opp is not None and seo_opp >= cfg["seo_threshold"]:
        if not is_no_website:
            if "SEO" not in services:
                services.append("SEO")

    # Conversion — samo ako website postoji i ima visok opportunity
    if has_website and conv_opp is not None and conv_opp >= cfg["conversion_threshold"]:
        services.append("Conversion Optimization")

    # Performance — samo sa evidence (response_time)
    if perf_opp is not None and perf_opp >= 6:
        # performance ima evidence (rt/http)
        rt = lead.get("response_time_ms")
        http = lead.get("http_status")
        if rt is not None or (http is not None and http >= 400):
            services.append("Performance Optimization")

    # ukloni duplikate, sačuvaj redosled
    seen = set()
    unique = []
    for s in services:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return unique


def classify_lead(business_strength: int | None, recommended_services: list[str], priority_level: str) -> str:
    if not recommended_services:
        if priority_level == "LOW":
            return "Low Priority"
        return "Manual Review"
    # broji nezavisne tipove
    types = set()
    for s in recommended_services:
        if "Website" in s:
            types.add("Website")
        elif "Local SEO" in s:
            types.add("Local SEO")
        elif s == "SEO":
            types.add("SEO")
        elif "Conversion" in s:
            types.add("Conversion")
        elif "Performance" in s:
            types.add("Performance")
        else:
            types.add(s)
    if len(types) >= 2:
        return "Multi-Service Opportunity"
    rs = recommended_services[0]
    if "Website Development" in rs or "Website Redesign" in rs:
        return "Website Opportunity"
    if "Local SEO" in rs:
        return "Local SEO Opportunity"
    if rs == "SEO":
        return "SEO Opportunity"
    if "Conversion" in rs:
        return "Conversion Opportunity"
    if "Performance" in rs:
        return "Performance Opportunity"
    return "Manual Review"


# ── Reason / Sales Angle / Confidence ──

def generate_lead_reason(lead: dict, business_strength: int | None, priority_score: int | None) -> str:
    """
    Faktualno, koristi samo dostupne podatke. Nikad ne spominji SEO/Conversion ako su None (N/A).
    """
    rating = lead.get("rating")
    reviews = lead.get("review_count")
    website = (lead.get("website") or "")
    has_website = bool(website.strip()) if isinstance(website, str) else False
    is_no_website = isinstance(website, str) and website.strip() == ""

    # business fraza — konzervativna
    business_phrase = ""
    if rating is not None and reviews is not None:
        if reviews <= 4:
            business_phrase = f"Limited signal with {rating} rating but only {reviews} review{'s' if reviews!=1 else ''}"
        elif business_strength is not None and business_strength >= 7:
            business_phrase = f"Strong local demand signal with {reviews} reviews and {rating} rating"
        elif business_strength is not None and business_strength >= 4:
            business_phrase = f"Moderate presence with {reviews} reviews and {rating} rating"
        else:
            business_phrase = f"{rating} rating, {reviews} reviews"
    elif rating is not None:
        business_phrase = f"{rating} rating"
    elif reviews is not None:
        if reviews <= 4:
            business_phrase = f"Only {reviews} review{'s' if reviews!=1 else ''} — weak demand signal"
        else:
            business_phrase = f"{reviews} reviews"
    elif business_strength is not None and business_strength >= 6:
        business_phrase = "Strong local presence"

    # opportunity — samo ako su skorovi != None
    w_opp = lead.get("website_opportunity_score")
    if w_opp is None:
        w_opp = lead.get("website_score")
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is None:
        seo_opp = lead.get("seo_score")
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is None:
        conv_opp = lead.get("conversion_score")
    local_opp = lead.get("local_seo_opportunity_score")
    perf_opp = lead.get("performance_opportunity_score")

    opp_parts = []
    if is_no_website and w_opp is not None and w_opp >= 7:
        opp_parts.append("no official website detected")
    elif has_website and w_opp is not None and w_opp >= 7:
        opp_parts.append("outdated website")

    # Local SEO — prikaži samo ako je visok i ima smisla
    if local_opp is not None and local_opp >= 6:
        # za no-website, local SEO je relevantan
        if is_no_website:
            opp_parts.append("high local SEO opportunity")
        else:
            # sa website-om, local SEO samo ako je audit pokazao
            if lead.get("automated_audit_status") == "Completed":
                opp_parts.append("high local SEO opportunity")

    if seo_opp is not None and seo_opp >= 6 and has_website:
        opp_parts.append("significant SEO opportunities" if seo_opp >= 7 else "SEO opportunity")
    if conv_opp is not None and conv_opp >= 6 and has_website:
        opp_parts.append("significant conversion weaknesses" if conv_opp >= 7 else "conversion opportunity")
    if perf_opp is not None and perf_opp >= 6:
        rt = lead.get("response_time_ms")
        if rt:
            opp_parts.append(f"performance opportunity ({rt}ms)")
        else:
            opp_parts.append("performance opportunity")

    if not opp_parts:
        if has_website and w_opp is not None and w_opp <= 3 and seo_opp is not None and seo_opp <= 3 and conv_opp is not None and conv_opp <= 3:
            opp_parts.append("no obvious digital opportunity detected from available audit data")
        elif is_no_website and business_strength is not None and business_strength <= 3:
            opp_parts.append("no official website detected, but current Google demand signal is still weak")
        elif not has_website and not is_no_website:
            # website is None (unknown)
            opp_parts.append("website status unknown")

    # kombinuj
    if business_phrase and opp_parts:
        # za weak business, naglasi weak
        if business_strength is not None and business_strength <= 3:
            return f"{business_phrase}, and {opp_parts[0]}."
        # za jak business
        if "Strong local demand" in business_phrase or "Strong Google" in business_phrase:
            return f"{business_phrase}, but {', '.join(opp_parts[:2])}."
        else:
            return f"{business_phrase} with {', '.join(opp_parts[:2])}."
    elif opp_parts:
        return f"{opp_parts[0].capitalize()}."
    elif business_phrase:
        return f"{business_phrase}."
    else:
        return "Manual review — insufficient data."


def generate_sales_angle(recommended_services: list[str], lead_type: str) -> str:
    if not recommended_services:
        return "Manual review — no clear service angle without further research."
    primary = recommended_services[0]
    if "Website Development" in primary:
        return "Focus on turning existing local demand into website visits and enquiries."
    if "Website Redesign" in primary:
        return "Focus on modernizing the existing site to improve mobile experience and trust."
    if "Local SEO" in primary or lead_type == "Local SEO Opportunity":
        return "Focus on increasing visibility for people searching for this service locally."
    if "SEO" in primary:
        return "Focus on improving organic visibility and attracting high-intent search traffic."
    if "Conversion" in primary:
        return "Focus on turning existing website visitors into enquiries/bookings."
    if "Performance" in primary:
        return "Focus on improving website speed and reducing friction, especially on mobile."
    # multi-service
    if len(recommended_services) >= 2:
        return f"Focus on the highest-impact combination of {', '.join(recommended_services[:2])}."
    return f"Focus on {primary.lower()} as the primary value driver."


def calculate_confidence(lead: dict, business_strength: int | None) -> str:
    has_rating = lead.get("rating") is not None
    has_reviews = lead.get("review_count") is not None
    website = lead.get("website")
    has_website = isinstance(website, str) and website.strip() != ""
    has_no_website = isinstance(website, str) and website.strip() == ""
    is_unknown = website is None
    audit_status = (lead.get("automated_audit_status") or "").strip()

    if is_unknown:
        return "Low"
    signals = 0
    if has_rating:
        signals += 1
    if has_reviews:
        signals += 1
    if has_no_website or has_website:
        signals += 1
    if audit_status == "Completed":
        signals += 1
    # za no-website, ne treba audit za High, ali treba rating+reviews+confirmed no-website
    if has_no_website and has_rating and has_reviews:
        return "High"
    if signals >= 3 and has_rating and has_reviews:
        return "High"
    if signals >= 2:
        return "Medium"
    return "Low"


def generate_opportunity_evidence(lead: dict) -> str:
    """
    Kratak JSON string sa evidence za svaku priliku, da sistem bude trustable.
    """
    import json
    evidence = {}
    w_opp = lead.get("website_opportunity_score")
    if w_opp is not None and w_opp >= 7:
        if (lead.get("website") or "") == "":
            evidence["Website Development"] = "No official website detected."
        else:
            evidence["Website Redesign"] = f"Website on outdated platform or high opportunity (score {w_opp})."
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is not None and seo_opp >= 6 and lead.get("website"):
        # pokusaj iz audit_data
        try:
            data = json.loads(lead.get("audit_data_json") or "{}")
            missing = []
            if not data.get("title"):
                missing.append("missing title")
            if not data.get("meta_description"):
                missing.append("missing meta description")
            if not data.get("h1"):
                missing.append("missing H1")
            evidence["SEO"] = ", ".join(missing) if missing else f"SEO opportunity (score {seo_opp})."
        except Exception:
            evidence["SEO"] = f"SEO opportunity (score {seo_opp})."
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is not None and conv_opp >= 6 and lead.get("website"):
        try:
            data = json.loads(lead.get("audit_data_json") or "{}")
            missing = []
            if not data.get("has_tel_link"):
                missing.append("no tel link")
            if not data.get("has_form"):
                missing.append("no contact form")
            if not data.get("has_booking_link"):
                missing.append("no booking flow")
            evidence["Conversion"] = ", ".join(missing) if missing else f"Conversion opportunity (score {conv_opp})."
        except Exception:
            evidence["Conversion"] = f"Conversion opportunity (score {conv_opp})."
    local_opp = lead.get("local_seo_opportunity_score")
    if local_opp is not None and local_opp >= 6:
        evidence["Local SEO"] = "Strong Google presence but limited owned web presence." if (lead.get("website") or "") == "" else f"Local SEO opportunity (score {local_opp})."
    perf_opp = lead.get("performance_opportunity_score")
    if perf_opp is not None and perf_opp >= 6:
        rt = lead.get("response_time_ms")
        evidence["Performance"] = f"Response time: {rt}ms." if rt else f"Performance opportunity (score {perf_opp})."
    return json.dumps(evidence, ensure_ascii=False)


def prioritize_lead(lead: dict) -> dict:
    """
    Glavna funkcija — prima lead dict, vraća isti dict dopunjen sa
    business_strength_score, priority_score, priority, lead_type, recommended_services, lead_reason, sales_angle, prioritization_confidence, opportunity_evidence, prioritization_updated_at
    """
    from datetime import datetime, timezone
    import json

    bs = calculate_business_strength(lead)
    lead["business_strength_score"] = bs

    w_opp = lead.get("website_opportunity_score")
    if w_opp is None:
        w_opp = lead.get("website_score")
    seo_opp = lead.get("seo_opportunity_score")
    if seo_opp is None:
        seo_opp = lead.get("seo_score")
    conv_opp = lead.get("conversion_opportunity_score")
    if conv_opp is None:
        conv_opp = lead.get("conversion_score")
    local_opp = lead.get("local_seo_opportunity_score")
    perf_opp = lead.get("performance_opportunity_score")

    priority = calculate_priority(bs, w_opp, seo_opp, conv_opp, local_opp, perf_opp)
    level = calculate_priority_level(priority)

    services = recommend_services(lead, bs)
    lead_type = classify_lead(bs, services, level)
    reason = generate_lead_reason(lead, bs, priority)
    angle = generate_sales_angle(services, lead_type)
    confidence = calculate_confidence(lead, bs)
    evidence = generate_opportunity_evidence(lead)

    lead["priority_score"] = priority
    lead["priority"] = level
    lead["lead_type"] = lead_type
    lead["recommended_services"] = json.dumps(services, ensure_ascii=False)
    lead["lead_reason"] = reason
    lead["sales_angle"] = angle
    lead["prioritization_confidence"] = confidence
    lead["opportunity_evidence"] = evidence
    lead["prioritization_updated_at"] = datetime.now(timezone.utc).isoformat()

    return lead
