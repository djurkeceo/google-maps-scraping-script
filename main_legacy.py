"""
Zeltro Lead Scraper — Google Maps
==================================
Skrejpuje firme sa Google Maps i filtrira:
  1. Firme koje NEMAJU sajt
  2. Firme koje imaju ZASTAREO sajt (Wix, Squarespace, itd.)

Output: zeltro_leads.csv
"""

import asyncio
import csv
import re
import time

import pandas as pd
from playwright.async_api import async_playwright

# ─────────────────────────────────────────
# KONFIGURACIJA — menjaj ovo
# ─────────────────────────────────────────

SEARCHES = [
    # "restorani Subotica",
    # "kafici Subotica",
    # "frizeri Subotica",
    # "beauty salon Subotica",
    # "auto servis Subotica",
    "teretana Subotica",
    # "apoteka Subotica",
    # "zubar Subotica",
]

# Platforme koje smatramo "zastarelim" ili "losim"
OUTDATED_PLATFORMS = [
    "wix.com",
    "squarespace.com",
    "weebly.com",
    "jimdo.com",
    "webnode",
    "blogger.com",
    "wordpress.com",
    "yolasite.com",
    "site123.com",
    "strikingly.com",
]

MAX_RESULTS_PER_SEARCH = 30

# ─────────────────────────────────────────
# HELPER FUNKCIJE
# ─────────────────────────────────────────


def classify_website(url: str) -> str:
    if not url or url.strip() == "":
        return "no_website"
    url_lower = url.lower()
    for platform in OUTDATED_PLATFORMS:
        if platform in url_lower:
            return "outdated"
    return "has_website"


def clean_phone(phone: str) -> str:
    if not phone:
        return ""
    return re.sub(r"[^\d+\s\-\(\)]", "", phone).strip()


# ─────────────────────────────────────────
# GLAVNI SCRAPER
# ─────────────────────────────────────────


async def scrape_google_maps(search_query: str, max_results: int = 20) -> list:
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False
        )  # False = vidis browser, lakse za debug
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="sr-RS",
        )
        page = await context.new_page()

        # Idi na Google Maps pretragu
        search_url = (
            f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
        )
        print(f"\n🔍 Pretražujem: {search_query}")

        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        # Prihvati cookies ako se pojavi dijalog
        try:
            accept_btn = page.locator(
                'button:has-text("Accept all"), button:has-text("Prihvati sve"), form:nth-child(2) button'
            )
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await asyncio.sleep(2)
        except Exception:
            pass

        # Skroluj kroz listu rezultata
        try:
            results_panel = page.locator('div[role="feed"]')
            for _ in range(6):
                await results_panel.evaluate("el => el.scrollTop += 1200")
                await asyncio.sleep(1.5)
        except Exception:
            pass

        # Pokupi URL-ove listinga (URL-ovi su stabilni i ne gube se posle navigacije)
        listing_elements = await page.locator(
            'div[role="feed"] > div > div a[href*="/maps/place/"]'
        ).all()
        listing_urls = []
        for el in listing_elements:
            href = await el.get_attribute("href")
            if href and href not in listing_urls:
                listing_urls.append(href)

        print(f"   Pronađeno {len(listing_urls)} listinga")

        count = 0
        for url in listing_urls[:max_results]:
            if count >= max_results:
                break
            try:
                # Navigiraj direktno na URL — ne klikci na stale lokatore
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2.5)

                # Izvuci ime firme
                name = ""
                try:
                    name = await page.locator("h1").first.inner_text(timeout=5000)
                except Exception:
                    pass

                if not name or name.strip() == "":
                    await page.go_back(wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)
                    continue

                # Izvuci adresu
                address = ""
                try:
                    addr_el = page.locator('button[data-item-id="address"]')
                    if await addr_el.count() > 0:
                        address = await addr_el.first.inner_text(timeout=3000)
                except Exception:
                    pass

                # Izvuci telefon
                phone = ""
                try:
                    phone_el = page.locator('button[data-item-id^="phone"]')
                    if await phone_el.count() > 0:
                        phone = await phone_el.first.inner_text(timeout=3000)
                        phone = clean_phone(phone)
                except Exception:
                    pass

                # Izvuci website
                website = ""
                try:
                    web_el = page.locator('a[data-item-id="authority"]')
                    if await web_el.count() > 0:
                        website = (
                            await web_el.first.get_attribute("href", timeout=3000) or ""
                        )
                except Exception:
                    pass

                # Klasifikuj
                website_status = classify_website(website)

                if website_status in ["no_website", "outdated"]:
                    lead = {
                        "ime_firme": name.strip(),
                        "kategorija": search_query.split(" ")[0],
                        "grad": "Subotica",
                        "adresa": address.strip(),
                        "telefon": phone,
                        "website": website,
                        "status_sajta": "Nema sajt"
                        if website_status == "no_website"
                        else f"Zastareo ({website})",
                        "prioritet": "Visok"
                        if website_status == "no_website"
                        else "Srednji",
                        "kontaktiran": "Ne",
                        "napomena": "",
                    }
                    results.append(lead)
                    status_emoji = (
                        "❌ Nema sajt"
                        if website_status == "no_website"
                        else f"⚠️  Zastareo"
                    )
                    print(f"   ✅ Lead #{count + 1}: {name.strip()} — {status_emoji}")
                    count += 1
                else:
                    print(f"   ⏭️  Preskačem: {name.strip()} (ima dobar sajt)")

                # Vrati se na listu rezultata
                await page.go_back(wait_until="domcontentloaded")
                await asyncio.sleep(1.5)

            except Exception as e:
                print(f"   ⚠️  Greška: {e}")
                try:
                    await page.go_back(wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)
                except Exception:
                    # Ako go_back ne radi, idi na search URL
                    try:
                        await page.goto(
                            search_url, wait_until="domcontentloaded", timeout=60000
                        )
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                continue

        await browser.close()

    return results


async def main():
    all_leads = []
    seen_names = set()

    print("=" * 50)
    print("  ZELTRO LEAD SCRAPER — Google Maps")
    print("=" * 50)

    for query in SEARCHES:
        leads = await scrape_google_maps(query, MAX_RESULTS_PER_SEARCH)
        for lead in leads:
            if lead["ime_firme"] not in seen_names:
                seen_names.add(lead["ime_firme"])
                all_leads.append(lead)
        # Pauza izmedju pretraga
        print(f"\n   ⏳ Pauza 4s pre sledece pretrage...")
        await asyncio.sleep(4)

    # Export u CSV
    if all_leads:
        df = pd.DataFrame(all_leads)
        output_path = "zeltro_leads_teretane.csv"
        df.to_csv(output_path, index=False, encoding="utf-8-sig")

        print("\n" + "=" * 50)
        print(f"  ✅ GOTOVO — {len(all_leads)} leadova pronađeno")
        print(f"  📁 Sačuvano: zeltro_leads.csv")
        print("=" * 50)

        no_website = len([l for l in all_leads if "Nema" in l["status_sajta"]])
        outdated = len([l for l in all_leads if "Zastareo" in l["status_sajta"]])
        print(f"\n  📊 Statistike:")
        print(f"     ❌ Bez sajta:     {no_website} firmi")
        print(f"     ⚠️  Zastareo sajt: {outdated} firmi")
    else:
        print("\n⚠️  Nisu pronađeni leadovi.")


if __name__ == "__main__":
    asyncio.run(main())
