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


async def _extract_rating_and_reviews(page) -> tuple[float | None, int | None]:
    """
    Pokušava da izvuče rating i broj recenzija.
    Google Maps prikazuje npr. "4.8 ★ (137)" ili "4,8 · 137 recenzija"
    """
    rating = None
    review_count = None

    try:
        # Rating — traži span sa aria-label koji sadrži zvezdice
        # Prvo pokušaj preko aria-label
        rating_el = page.locator('span[role="img"][aria-label*="stars"], span[aria-label*="stars"]')
        if await rating_el.count() > 0:
            aria = await rating_el.first.get_attribute("aria-label") or ""
            # "5.0 stars" ili "4,8 stars"
            m = re.search(r"(\d+[.,]\d+)", aria)
            if m:
                rating = float(m.group(1).replace(",", "."))

        # Fallback: traži tekst koji liči na rating pored broja recenzija
        if rating is None:
            # Pokušaj preko JS evaluacije — često je rating u tekstu pored recenzija
            content = await page.content()
            # Pattern: 4.8 (137)  ili 4,8 · 137 recenzija
            m = re.search(r"(\d+[.,]\d+)\s*[★\·\.]?\s*\(?\s*(\d+)\s*(?:reviews?|recenzija|ocene)?\s*\)?", content, re.IGNORECASE)
            if m:
                rating = float(m.group(1).replace(",", "."))
                review_count = int(m.group(2))

        # Review count — ako nije već pronađen
        if review_count is None:
            # Traži button/link koji sadrži broj recenzija
            review_locators = [
                'button:has-text("recenzija")',
                'button:has-text("reviews")',
                'a:has-text("recenzija")',
                'span:has-text("recenzija")',
            ]
            for sel in review_locators:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    txt = await loc.first.inner_text(timeout=2000)
                    m = re.search(r"(\d+)", txt.replace(".", "").replace(",", ""))
                    if m:
                        review_count = int(m.group(1))
                        break

            # Fallback: aria-label sa brojem recenzija
            if review_count is None:
                all_aria = page.locator('[aria-label*="recenz"]')
                if await all_aria.count() > 0:
                    txt = await all_aria.first.get_attribute("aria-label") or ""
                    m = re.search(r"(\d+)", txt)
                    if m:
                        review_count = int(m.group(1))

    except Exception:
        pass

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

                # Website
                website = ""
                try:
                    web_el = page.locator('a[data-item-id="authority"]')
                    if await web_el.count() > 0:
                        website = await web_el.first.get_attribute("href", timeout=3000) or ""
                except Exception:
                    pass

                # Rating & Review Count
                rating, review_count = await _extract_rating_and_reviews(page)

                # Social
                instagram, facebook = await _extract_social_links(page)

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
