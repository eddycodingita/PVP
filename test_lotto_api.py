"""
Intercetta l'endpoint del lotto per trovare gli allegati PDF.
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
            # Cattura TUTTE le chiamate API (non solo ve-ms)
            if any(x in url for x in [
                "ve-ms", "ric-ms", "allegati", "documenti", "lotti", 
                "beni", "perizia", "files", "download"
            ]):
                try:
                    data = await response.json()
                    responses[url] = data
                    print(f"API: {url}")
                except Exception:
                    pass

        page.on("response", on_response)

        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(8)  # aspetta di più per caricare tutto

        print(f"\n{'='*60}")
        print(f"Totale API intercettate: {len(responses)}")
        
        # Cerca specificamente allegati in tutte le risposte
        for url, data in responses.items():
            body = data.get("body") or data
            body_str = json.dumps(body, ensure_ascii=False)
            
            # Cerca keywords documento
            if any(k in body_str.lower() for k in ["pdf", "allegat", "document", "file", "scaric"]):
                print(f"\n{'='*60}")
                print(f"TROVATO POSSIBILE DOCUMENTO in: {url}")
                print(json.dumps(body, indent=2, ensure_ascii=False)[:2000])
                
                # Salva
                fname = "allegati_" + url.split("/")[-1][:30].replace("?","_") + ".json"
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                print(f"Salvato: {fname}")

        # Cerca endpoint lotto specifico
        lotto_id = 1597429  # dal test precedente
        lotto_urls = [
            f"https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/lotti/{lotto_id}",
            f"https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/lotti/{lotto_id}/allegati",
            f"https://pvp.giustizia.it/ve-3f723b85-986a1b71/ve-ms/lotti/{lotto_id}/documenti",
        ]
        print(f"\n{'='*60}")
        print("Endpoint lotto intercettati:")
        for url in responses.keys():
            if str(lotto_id) in url:
                print(f"  {url}")

        await browser.close()

asyncio.run(test())
