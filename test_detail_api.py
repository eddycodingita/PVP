"""
Intercetta e salva le risposte API complete del dettaglio asta.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, json
from playwright.async_api import async_playwright

TEST_ID  = 1738450
TEST_URL = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={TEST_ID}"

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx     = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            locale="it-IT",
        )
        page = await ctx.new_page()

        responses = {}

        async def on_response(response):
            url = response.url
            if "ve-ms/vendite" in url or "ve-ms/lotti" in url:
                try:
                    data = await response.json()
                    responses[url] = data
                    print(f"Intercettato: {url[:90]}")
                except Exception as e:
                    print(f"Errore parsing {url[:60]}: {e}")

        page.on("response", on_response)

        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)

        print(f"\nRisposte intercettate: {len(responses)}")

        for url, data in responses.items():
            print(f"\n{'='*60}")
            print(f"URL: {url[:90]}")
            body = data.get("body") or data
            print(f"Struttura body: {json.dumps(body, indent=2, ensure_ascii=False)[:3000]}")

            # Salva su file
            fname = url.split("/")[-1].split("?")[0][:30] + ".json"
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Salvato: {fname}")

        await browser.close()

asyncio.run(test())
