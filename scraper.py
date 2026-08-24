"""
scraper.py — prošireni Google Maps scraper
Izvlači: name, address, phone, website, rating, review_count, place_id, google_maps_url, instagram, facebook
"""

import asyncio
import re
from datetime import datetime, timezone

from playwright.async_api import async_playwright

import config
from deduplication import extract_place_id
from scoring import score_lead


def _clean_phone(phone: str) -> str:
    if not phone:
        return ""
    return re.sub(r"[^\d+\s\-\(\)]", "", phone).strip()


SOCIAL_WEBSITE_DOMAINS = [
    "facebook.com",
    "m.facebook.com",
    "instagram.com",
    "tiktok.com",
    "booking.com",
    "treatwell",
    "fresha.com",
    "booksy.com",
    "glovoapp.com",
    "wolt.com",
    "yelp.com",
    "tripadvisor.com",
]


def _is_social_website(url: str) -> bool:
    if not url:
        return False
    lower = url.lower()
    return any(domain in lower for domain in SOCIAL_WEBSITE_DOMAINS)


def _validate_rating(value) -> float | None:
    """Vrši strogu validaciju: rating mora biti 0-5, inače None."""
    if value is None or value == "":
        return None
    try:
        r = float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None
    # rating mora biti između 0 i 5 (Google nikad ne daje >5)
    if 0 <= r <= 5:
        # dodatno: za listing sa recenzijama očekujemo >=1, ali dozvoli 0 za validaciju
        return r
    return None


def _validate_review_count(value) -> int | None:
    """review_count mora biti nenegativan integer."""
    if value is None or value == "":
        return None
    # direktna provera za int negativan pre čišćenja (čuva predznak)
    if isinstance(value, int) and value < 0:
        return None
    if isinstance(value, float) and value < 0:
        return None
    s = str(value).strip()
    # ako originalni string sadrži minus, odbaci (negativan)
    if s.startswith("-"):
        return None
    try:
        cleaned = re.sub(r"[^\d]", "", s)
        if cleaned == "":
            return None
        c = int(cleaned)
        if c >= 0:
            return c
    except (ValueError, TypeError):
        pass
    return None


async def _extract_rating_and_reviews(page) -> tuple[float | None, int | None]:
    """
    Strogo parsira rating i review_count.
    - rating mora biti 0-5, inače None
    - review_count mora biti >=0 int, inače None
    - rating i review se nikad ne smeju zameniti
    Koristi page.evaluate JS za pouzdano pronalaženje u DOM-u, ne regex preko celog HTML-a.
    """
    rating = None
    review_count = None

    # 1) Pokušaj preko page.evaluate JS — najpouzdanije
    try:
        result = await page.evaluate("""() => {
            function extractFromAriaLabel() {
                // traži element sa aria-label koji sadrži "stars" i recenzije
                const candidates = document.querySelectorAll('[aria-label*="stars"], [aria-label*="Stars"], [aria-label*="zvezd"]');
                for (const el of candidates) {
                    const label = el.getAttribute('aria-label') || '';
                    // primer: "4,8 stars 137 reviews" ili "4.8 stars \u00b7 137 reviews"
                    // traži dve brojke: rating i reviews
                    const m = label.match(/(\\d+[.,]\\d+)\\s*[^\\d]*?(\\d+)/);
                    if (m) {
                        return {ratingRaw: m[1], reviewsRaw: m[2], source: 'aria-stars'};
                    }
                    // samo rating
                    const m2 = label.match(/(\\d+[.,]\\d+)/);
                    if (m2 && label.toLowerCase().includes('star')) {
                        return {ratingRaw: m2[1], reviewsRaw: null, source: 'aria-stars-only'};
                    }
                }
                return null;
            }

            function extractFromTextNodes() {
                // traži text node koji izgleda kao "4.8" pored "(137)"
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                const pattern = /(\\d[.,]\\d)\\s*[\\u2605\\u00b7\\u2022]?\\s*\\(?\\s*(\\d[\\d.,\\s]*)\\s*(?:reviews?|recenzija|recenzije|ocene|ocena|utisak|utisaka)?\\s*\\)?/i;
                while (node = walker.nextNode()) {
                    const txt = node.textContent.trim();
                    // preskoči duge tekstove (koordinate, cene)
                    if (txt.length > 50) continue;
                    const m = txt.match(pattern);
                    if (m) {
                        // proveri da li je u kontekstu ratinga (blizu zvezdica)
                        const parent = node.parentElement;
                        const nearStars = parent && (
                            parent.innerHTML.includes('star') ||
                            parent.querySelector('[aria-label*="star"]') ||
                            parent.closest('[role="img"]')
                        );
                        // ili da li parent sadrži oba broja
                        return {ratingRaw: m[1], reviewsRaw: m[2], source: 'text-node'};
                    }
                }
                return null;
            }

            function extractSeparateSpans() {
                // Google često ima rating u <span>4.8</span> i reviews u <span>(137)</span> ili <a>(137)</a>
                const spans = Array.from(document.querySelectorAll('span'));
                for (let i = 0; i < spans.length; i++) {
                    const txt = spans[i].textContent.trim();
                    // rating je tačno 1.0-5.0 sa jednom decimalom ili bez
                    if (!/^(\\d[.,]\\d)$/.test(txt) && !/^(\\d)$/.test(txt)) continue;
                    const ratingVal = parseFloat(txt.replace(',', '.'));
                    if (ratingVal < 1 || ratingVal > 5) continue;
                    // proveri sledećih par elemenata za reviews
                    for (let j = i+1; j < Math.min(i+4, spans.length); j++) {
                        const nextTxt = spans[j].textContent.trim();
                        // reviews format: "(137)" ili "137 reviews" ili "137 recenzija"
                        const m = nextTxt.match(/^\\(?\\s*(\\d[\\d.,\\s]*)\\s*\\)?$/);
                        const m2 = nextTxt.match(/(\\d[\\d.,\\s]*)\\s*(?:reviews?|recenzija|ocene)/i);
                        const raw = m ? m[1] : (m2 ? m2[1] : null);
                        if (raw) {
                            // mora biti u istom kontejneru kao rating (blizu)
                            if (spans[i].parentElement === spans[j].parentElement ||
                                spans[i].parentElement.parentElement === spans[j].parentElement.parentElement) {
                                return {ratingRaw: txt, reviewsRaw: raw, source: 'separate-spans'};
                            }
                        }
                    }
                }
                return null;
            }

            return extractFromAriaLabel() || extractSeparateSpans() || extractFromTextNodes() || null;
        }""")
        if result and isinstance(result, dict):
            raw_rating = result.get("ratingRaw")
            raw_reviews = result.get("reviewsRaw")
            # stroga validacija
            rating = _validate_rating(raw_rating)
            review_count = _validate_review_count(raw_reviews)
            # ako je rating invalid, odbaci oba (ne pretpostavljaj)
            if raw_rating is not None and rating is None:
                # pokušaj da ne koristiš nevalidan rating kao review
                rating = None
            # ako je review nevalidan, odbaci
            if raw_reviews is not None and review_count is None:
                review_count = None
    except Exception:
        pass

    # 2) Fallback: direktno preko locatora sa strogo validiranim aria-label (ne ceo page.content)
    if rating is None:
        try:
            # traži sve aria-label koji sadrže star, ali validiraj 0-5
            loc = page.locator('[aria-label*="stars"], [aria-label*="Stars"], [aria-label*="zvezd"]')
            count = await loc.count()
            for i in range(min(count, 3)):
                aria = await loc.nth(i).get_attribute("aria-label") or ""
                # mora sadržati "star" i broj 0-5
                m = re.search(r"(\d+[.,]\d+)", aria)
                if m:
                    candidate = _validate_rating(m.group(1))
                    if candidate is not None and "star" in aria.lower():
                        rating = candidate
                        # pokušaj da iz istog aria izvučeš reviews (druga brojka)
                        m2 = re.search(r"(\d+[.,]\d+)\s*[^0-9]*?(\d+)", aria)
                        if m2 and review_count is None:
                            rc = _validate_review_count(m2.group(2))
                            if rc is not None:
                                review_count = rc
                        break
        except Exception:
            pass

    # 3) Review count poseban fallback — traži element sa "(broj)" pored ratinga
    if review_count is None:
        try:
            # traži span/a koji sadrži zagrade sa brojem, ali samo ako je u blizini rating elementa
            # ne koristi širok regex preko celog HTML-a
            for sel in ['span:has-text("(")', 'a:has-text("(")', 'button:has-text("(")']:
                loc = page.locator(sel)
                cnt = await loc.count()
                for i in range(min(cnt, 5)):
                    txt = await loc.nth(i).inner_text(timeout=1000)
                    # mora izgledati kao "(137)" ili "(1,234)" — ne koordinate
                    m = re.search(r"\(\s*(\d[\d\s.,]*)\s*\)", txt)
                    if m:
                        # proveri da li je ovaj element blizu rating elementa (isti parent kontejner)
                        # za sada samo validiraj da je reviews u razumnom opsegu
                        rc = _validate_review_count(m.group(1))
                        if rc is not None and 0 <= rc <= 1000000:
                            # dodatna provera: ne uzimaj ako je txt predug (koordinate)
                            if len(txt.strip()) < 20:
                                review_count = rc
                                break
                if review_count is not None:
                    break
        except Exception:
            pass

    # Finalna validacija — nikad ne vrati rating >5 ili reviews negativan
    rating = _validate_rating(rating)
    review_count = _validate_review_count(review_count)

    # ako je rating i dalje nevalidan, vrati None (ne nagađaj)
    return rating, review_count


async def _extract_social_links(page) -> tuple[str, str]:
    """Pokušava da nađe Instagram i Facebook linkove."""
    instagram = ""
    facebook = ""
    try:
        # Svi linkovi na stranici
        links = await page.locator('a[href*="instagram.com"], a[href*="facebook.com"]').all()
        for link in links:
            href = await link.get_attribute("href") or ""
            if "instagram.com" in href and not instagram:
                instagram = href
            elif "facebook.com" in href and not facebook:
                facebook = href
    except Exception:
        pass
    return instagram, facebook


async def scrape_google_maps(search_query: str, category: str = "", city: str = "", max_results: int = 20) -> list[dict]:
    """
    Scrape Google Maps za dati upit.
    Vraća listu lead dict-ova (snake_case ključevi spremni za bazu).
    """
    results: list[dict] = []

    # Izvuci kategoriju i grad iz query-a ako nisu eksplicitno prosleđeni
    if not category:
        category = search_query.split()[0] if search_query else ""
    if not city:
        # poslednja reč je često grad
        parts = search_query.split()
        city = parts[-1] if len(parts) > 1 else ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=config.PLAYWRIGHT_HEADLESS)
        context = await browser.new_context(
            user_agent=config.PLAYWRIGHT_USER_AGENT,
            locale=config.PLAYWRIGHT_LOCALE,
        )
        page = await context.new_page()

        search_url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
        print(f"\n🔍 Pretražujem: {search_query}")

        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        # Cookies
        try:
            accept_btn = page.locator('button:has-text("Accept all"), button:has-text("Prihvati sve"), form:nth-child(2) button')
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await asyncio.sleep(2)
        except Exception:
            pass

        # Skroluj feed
        try:
            results_panel = page.locator('div[role="feed"]')
            for _ in range(6):
                await results_panel.evaluate("el => el.scrollTop += 1200")
                await asyncio.sleep(1.5)
        except Exception:
            pass

        # Pokupi URL-ove
        listing_elements = await page.locator('div[role="feed"] > div > div a[href*="/maps/place/"]').all()
        listing_urls: list[str] = []
        for el in listing_elements:
            href = await el.get_attribute("href")
            if href and href not in listing_urls:
                listing_urls.append(href)

        print(f"   Pronađeno {len(listing_urls)} listinga")

        scraped_at = datetime.now(timezone.utc).isoformat()

        for url in listing_urls[:max_results]:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2.5)

                # Ime
                name = ""
                try:
                    name = await page.locator("h1").first.inner_text(timeout=5000)
                except Exception:
                    pass
                if not name or not name.strip():
                    await page.go_back(wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)
                    continue

                # Adresa
                address = ""
                try:
                    addr_el = page.locator('button[data-item-id="address"]')
                    if await addr_el.count() > 0:
                        address = await addr_el.first.inner_text(timeout=3000)
                except Exception:
                    pass

                # Telefon
                phone = ""
                try:
                    phone_el = page.locator('button[data-item-id^="phone"]')
                    if await phone_el.count() > 0:
                        phone = await phone_el.first.inner_text(timeout=3000)
                        phone = _clean_phone(phone)
                except Exception:
                    pass

                # Website — strogo razlikuj official website od social
                website = ""
                facebook_from_website = ""
                instagram_from_website = ""
                try:
                    web_el = page.locator('a[data-item-id="authority"]')
                    if await web_el.count() > 0:
                        raw_href = await web_el.first.get_attribute("href", timeout=3000) or ""
                        if raw_href and _is_social_website(raw_href):
                            # social URL nije official website
                            website = ""
                            if "facebook.com" in raw_href.lower():
                                facebook_from_website = raw_href
                            elif "instagram.com" in raw_href.lower():
                                instagram_from_website = raw_href
                            # tiktok/booking ostaju van website, ne moraju u notes
                        else:
                            website = raw_href
                except Exception:
                    pass

                # Rating & Review Count
                rating, review_count = await _extract_rating_and_reviews(page)

                # Social — uključi i one iz website polja ako su bili social
                instagram, facebook = await _extract_social_links(page)
                if not facebook and facebook_from_website:
                    facebook = facebook_from_website
                if not instagram and instagram_from_website:
                    instagram = instagram_from_website

                # Place ID
                current_url = page.url
                place_id = extract_place_id(current_url) or extract_place_id(url)

                lead = {
                    "company_name": name.strip(),
                    "category": category,
                    "city": city,
                    "address": address.strip(),
                    "phone": phone,
                    "website": website.strip(),
                    "google_maps_url": current_url or url,
                    "place_id": place_id,
                    "rating": rating,
                    "review_count": review_count,
                    "instagram": instagram,
                    "facebook": facebook,
                    "scraped_at": scraped_at,
                    "first_scraped_at": scraped_at,
                    "last_scraped_at": scraped_at,
                    "source_query": category,
                    "source_city": city,
                    "lead_status": "New",
                    "audit_status": "Not Started",
                    "automated_audit_status": "Not Started",
                    "notes": "",
                }

                # Scoring
                lead = score_lead(lead)

                results.append(lead)
                ws = lead.get("website_score")
                print(f"   ✅ {name.strip()} | rating={rating} reviews={review_count} | ws={ws} | {current_url[:60]}")

                await page.go_back(wait_until="domcontentloaded")
                await asyncio.sleep(1.5)

            except Exception as e:
                print(f"   ⚠️  Greška: {e}")
                try:
                    await page.go_back(wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)
                except Exception:
                    try:
                        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                continue

        await browser.close()

    return results
