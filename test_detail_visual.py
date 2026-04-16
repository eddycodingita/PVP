"""
Test visivo — apre la pagina dettaglio con browser visibile
per vedere cosa carica Playwright e intercettare i documenti.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TEST_ID  = 1738450  # asta di test
TEST_URL = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={TEST_ID}"

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # VISIBILE
        ctx     = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            locale="it-IT",
        )
        page = await ctx.new_page()

        api_responses = []

        async def on_response(response):
            url = response.url
            if any(x in url for x in ["annunci", "vendite", "lotti", "allegati", "documenti"]):
                try:
                    data = await response.json()
                    api_responses.append({"url": url, "data": data})
                    print(f"  API: {url[:80]}")
                    print(f"  Chiavi: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                except Exception:
                    pass

        page.on("response", on_response)

        print(f"Navigo: {TEST_URL}")
        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)

        # Prendi HTML
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Cerca link PDF
        print("\n--- LINK PDF TROVATI ---")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if ".pdf" in href.lower() or "scarica" in text.lower() or "download" in href.lower():
                print(f"  [{text}] → {href}")

        # Cerca sezione allegati
        print("\n--- SEZIONE ALLEGATI ---")
        for el in soup.find_all(string=lambda t: t and "allegat" in t.lower()):
            parent = el.parent
            print(f"  {parent.name}: {str(parent)[:200]}")

        # Titolo pagina
        title = soup.find("title")
        print(f"\n--- TITOLO PAGINA ---\n  {title.get_text() if title else 'N/A'}")

        # Salva HTML per ispezione
        with open("detail_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("\nHTML salvato in detail_debug.html")

        print(f"\nAPI intercettate: {len(api_responses)}")
        for r in api_responses:
            print(f"  {r['url'][:80]}")

        input("\nPremi INVIO per chiudere...")
        await browser.close()

asyncio.run(test())
