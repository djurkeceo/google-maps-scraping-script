"""
website_audit.py — lagani HTTP/HTML audit za svaki lead
Ne koristi Lighthouse/browser, samo requests/urllib + regex/BeautifulSoup ako je dostupno.
Mora biti non-blocking: timeout, exception handling, max redirects.
"""

import re
import time
import json
from urllib.parse import urljoin, urlparse

# probaj da importuješ requests, inače fallback na urllib
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sr-RS,sr;q=0.9,en;q=0.8",
}

TIMEOUT = 10
MAX_REDIRECTS = 5


def _fetch(url: str, timeout: int = TIMEOUT) -> tuple[int | None, str, int | None, str | None]:
    """
    Vrati (status_code, html, response_time_ms, error)
    Koristi requests ako postoji, inače urllib.
    """
    if not url or not url.strip():
        return None, "", None, "no_url"

    # normalizuj URL
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")

    start = time.time()
    try:
        if HAS_REQUESTS:
            resp = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            elapsed = int((time.time() - start) * 1000)
            # ograniči veličinu
            html = resp.text[:500000]  # max 500k
            return resp.status_code, html, elapsed, None
        else:
            import urllib.request
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                elapsed = int((time.time() - start) * 1000)
                html = r.read().decode("utf-8", errors="ignore")[:500000]
                return r.status, html, elapsed, None
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        err = str(e)[:200]
        # klasifikuj gresku
        if "timeout" in err.lower() or "timed out" in err.lower():
            return None, "", elapsed, f"timeout: {err}"
        elif "name or service not known" in err.lower() or "getaddrinfo" in err.lower():
            return None, "", elapsed, f"dns_error: {err}"
        elif "connection" in err.lower():
            return None, "", elapsed, f"connection_error: {err}"
        else:
            return None, "", elapsed, f"fetch_error: {err}"


def _parse_html(html: str, base_url: str) -> dict:
    """Parsira HTML i vraća dict sa title, meta, h1, itd."""
    result = {
        "title": "",
        "meta_description": "",
        "h1": "",
        "h1_count": 0,
        "viewport": "",
        "canonical": "",
        "has_tel_link": False,
        "has_mailto": False,
        "has_form": False,
        "has_booking_link": False,
        "has_cta": False,
        "has_offer": False,
        "has_instagram": False,
        "has_facebook": False,
        "has_maps_link": False,
        "all_links": [],
    }
    if not html:
        return result

    if HAS_BS4:
        try:
            soup = BeautifulSoup(html, "html.parser")
            # title
            if soup.title and soup.title.string:
                result["title"] = soup.title.string.strip()[:200]
            # meta description
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                result["meta_description"] = meta["content"].strip()[:300]
            # h1
            h1s = soup.find_all("h1")
            result["h1_count"] = len(h1s)
            if h1s:
                result["h1"] = h1s[0].get_text(strip=True)[:200]
            # viewport
            vp = soup.find("meta", attrs={"name": "viewport"})
            if vp and vp.get("content"):
                result["viewport"] = vp["content"].strip()[:200]
            # canonical
            canon = soup.find("link", attrs={"rel": "canonical"})
            if canon and canon.get("href"):
                result["canonical"] = canon["href"].strip()[:300]

            # links
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True).lower()
                result["all_links"].append(href[:300])
                if href.startswith("tel:"):
                    result["has_tel_link"] = True
                if href.startswith("mailto:"):
                    result["has_mailto"] = True
                if "instagram.com" in href:
                    result["has_instagram"] = True
                if "facebook.com" in href:
                    result["has_facebook"] = True
                if "google.com/maps" in href or "goo.gl/maps" in href or "maps.google" in href:
                    result["has_maps_link"] = True
                # booking
                if any(kw in href.lower() or kw in text for kw in ["booking", "rezerv", "zakaz", "book", "appoint"]):
                    result["has_booking_link"] = True
                # CTA
                if any(kw in text for kw in ["kontakt", "contact", "prijav", "upiši", "pozovi", "call", "rezervi", "book now", "get started", "join"]):
                    result["has_cta"] = True
                # offer
                if any(kw in text for kw in ["probni", "free trial", "akcija", "popust", "offer", "promo", "besplat"]):
                    result["has_offer"] = True

            # forms
            if soup.find("form"):
                result["has_form"] = True

            # CTA fallback: button check
            if not result["has_cta"]:
                for btn in soup.find_all(["button", "a"]):
                    txt = btn.get_text(strip=True).lower()
                    if any(kw in txt for kw in ["kontakt", "contact", "rezerv", "book", "prijav"]):
                        result["has_cta"] = True
                        break

        except Exception:
            pass
    else:
        # regex fallback — minimalno
        try:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if m:
                result["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:200]
            m = re.search(r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*name=["\']description["\']', html, re.IGNORECASE)
            if m:
                result["meta_description"] = m.group(1).strip()[:300]
            h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
            result["h1_count"] = len(h1s)
            if h1s:
                result["h1"] = re.sub(r"<[^>]+>", "", h1s[0]).strip()[:200]
            if 'name="viewport"' in html.lower():
                m2 = re.search(r'<meta[^>]+name=["\']viewport["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
                if m2:
                    result["viewport"] = m2.group(1)[:200]
            # simple link checks
            result["has_tel_link"] = "tel:" in html.lower()
            result["has_mailto"] = "mailto:" in html.lower()
            result["has_form"] = "<form" in html.lower()
            result["has_instagram"] = "instagram.com" in html.lower()
            result["has_facebook"] = "facebook.com" in html.lower()
            result["has_maps_link"] = "google.com/maps" in html.lower()
            result["has_booking_link"] = any(kw in html.lower() for kw in ["booking", "rezerv", "zakaz"])
            result["has_cta"] = any(kw in html.lower() for kw in ["kontakt", "contact", "rezerv"])
            result["has_offer"] = any(kw in html.lower() for kw in ["probni", "akcija", "popust"])
        except Exception:
            pass

    return result


def _check_robots_and_sitemap(base_url: str, timeout: int = 5) -> tuple[bool | None, bool | None]:
    """Proveri robots.txt i sitemap.xml postojanje (light check)."""
    robots_exists = None
    sitemap_exists = None
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for path, target in [("/robots.txt", "robots"), ("/sitemap.xml", "sitemap")]:
        try:
            url = origin + path
            if HAS_REQUESTS:
                r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
                exists = r.status_code == 200 and len(r.text) > 10
            else:
                import urllib.request
                req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    exists = resp.status == 200
            if target == "robots":
                robots_exists = exists
            else:
                sitemap_exists = exists
        except Exception:
            if target == "robots":
                robots_exists = False
            else:
                sitemap_exists = False

    return robots_exists, sitemap_exists


def audit_website(url: str, timeout: int = TIMEOUT) -> dict:
    """
    Glavna audit funkcija.
    Vraća dict sa svim proverama + automated_audit_status.
    Ako website nije dostupan, automated_audit_status = 'Unable to Audit'
    """
    if not url or not url.strip():
        return {
            "automated_audit_status": "Unable to Audit",
            "error": "no_website",
            "http_status": None,
            "response_time_ms": None,
            "https": None,
            "title": "",
            "meta_description": "",
            "h1": "",
            "audit_data_json": json.dumps({"error": "no_website"}, ensure_ascii=False),
        }

    # normalizuj
    orig_url = url.strip()
    if not orig_url.startswith("http"):
        orig_url = "https://" + orig_url.lstrip("/")

    https = orig_url.startswith("https://")
    status, html, elapsed, error = _fetch(orig_url, timeout=timeout)

    if error or status is None or status >= 400:
        # pokušaj http fallback ako je https fail
        if https and (error or status in (None, 0)):
            alt = orig_url.replace("https://", "http://")
            status2, html2, elapsed2, error2 = _fetch(alt, timeout=timeout)
            if status2 and status2 < 400:
                status, html, elapsed, error = status2, html2, elapsed2, error2

    if error or not html or status is None or status >= 400:
        return {
            "automated_audit_status": "Unable to Audit",
            "error": error or f"http_{status}",
            "http_status": status,
            "response_time_ms": elapsed,
            "https": https,
            "title": "",
            "meta_description": "",
            "h1": "",
            "audit_data_json": json.dumps({"error": error or f"http_{status}", "http_status": status}, ensure_ascii=False),
        }

    parsed = _parse_html(html, orig_url)
    # robots/sitemap — optional, ne blokira
    try:
        robots_exists, sitemap_exists = _check_robots_and_sitemap(orig_url, timeout=5)
    except Exception:
        robots_exists, sitemap_exists = None, None

    audit_data = {
        "http_status": status,
        "response_time_ms": elapsed,
        "https": https,
        "title": parsed["title"],
        "meta_description": parsed["meta_description"],
        "h1": parsed["h1"],
        "h1_count": parsed["h1_count"],
        "viewport": parsed["viewport"],
        "canonical": parsed["canonical"],
        "robots_txt_exists": robots_exists,
        "sitemap_exists": sitemap_exists,
        **{k: v for k, v in parsed.items() if k not in ("title", "meta_description", "h1", "viewport", "canonical")},
        "error": None,
    }

    return {
        "automated_audit_status": "Completed",
        "http_status": status,
        "response_time_ms": elapsed,
        "https": https,
        "title": parsed["title"],
        "meta_description": parsed["meta_description"],
        "h1": parsed["h1"],
        "audit_data_json": json.dumps(audit_data, ensure_ascii=False),
        **audit_data,
    }


def audit_leads_batch(leads: list[dict], max_audit: int = 50, delay: float = 0.5) -> tuple[list[dict], dict]:
    """
    Auditira batch leadova koji imaju website.
    Vraća (updated_leads, stats)
    Non-blocking: svaki fail se loguje, ne zaustavlja batch.
    """
    import time as _time
    stats = {"audited": 0, "failures": 0, "no_website": 0}
    updated = []

    for lead in leads:
        website = (lead.get("website") or "").strip()
        if not website:
            # nema website — ne auditiramo, status ostaje Not Started ili Unable?
            # Za nema sajta, automated = Not Applicable ili Unable to Audit — ostavimo Not Started
            lead["automated_audit_status"] = "Not Started"
            stats["no_website"] += 1
            updated.append(lead)
            continue

        if stats["audited"] >= max_audit:
            # limit da ne preoptereti
            lead.setdefault("automated_audit_status", "Not Started")
            updated.append(lead)
            continue

        try:
            result = audit_website(website, timeout=TIMEOUT)
            lead["automated_audit_status"] = result.get("automated_audit_status", "Unable to Audit")
            lead["audit_data_json"] = result.get("audit_data_json", "")
            lead["http_status"] = result.get("http_status")
            lead["response_time_ms"] = result.get("response_time_ms")
            # čuvaj title/meta za scoring ako treba
            if result.get("automated_audit_status") == "Completed":
                stats["audited"] += 1
            else:
                stats["failures"] += 1
                print(f"   Website audit failed for {lead.get('company_name','?')} ({website}): {result.get('error')}")
        except Exception as e:
            lead["automated_audit_status"] = "Unable to Audit"
            lead["audit_data_json"] = json.dumps({"error": str(e)[:200]}, ensure_ascii=False)
            stats["failures"] += 1
            print(f"   Website audit failed for {lead.get('company_name','?')}: {e}")

        updated.append(lead)
        # mali delay da ne hammerujemo
        if delay > 0:
            _time.sleep(delay)

    return updated, stats
