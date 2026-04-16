"""
Test downloader su un'asta specifica con allegati noti.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# Asta con 2 allegati noti (testata prima)
TEST_ID  = "1738450"
TEST_URL = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={TEST_ID}"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx     = await browser.new_context(
            user_agent=USER_AGENT,
            locale="it-IT",
            accept_downloads=True,
        )
        page = await ctx.new_page()

        api_data  = {}
        api_event = asyncio.Event()

        async def on_response(response):
            if f"vendite/{TEST_ID}/restricted" in response.url:
                try:
                    data = await response.json()
                    api_data.update(data)
                    api_event.set()
                    print(f"API intercettata: {response.url[:70]}")
                except Exception:
                    api_event.set()

        page.on("response", on_response)

        await page.goto(TEST_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            await asyncio.wait_for(api_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            print("Timeout API!")

        # Aspetta Angular
        try:
            await page.wait_for_selector("text=Allegati", timeout=10000)
            print("Sezione Allegati trovata!")
            await asyncio.sleep(2)
        except:
            print("Sezione Allegati NON trovata")

        # Clicca bottoni
        buttons = await page.query_selector_all("button:has-text('Scarica')")
        print(f"Bottoni Scarica: {len(buttons)}")

        docs = []
        for i, btn in enumerate(buttons):
            try:
                async with page.expect_download(timeout=8000) as dl_info:
                    await btn.click()
                dl   = await dl_info.value
                nome = dl.suggested_filename
                url  = dl.url
                print(f"  Doc {i+1}: {nome} → {url[:80]}")
                docs.append({"nome": nome, "url": url})
                await dl.cancel()
            except Exception as e:
                print(f"  Doc {i+1}: errore — {e}")

        # Dati API
        body = api_data.get("body") or api_data
        lotto = body.get("lotto") or {}
        ind   = lotto.get("indirizzo") or {}
        print(f"\nDati API:")
        print(f"  Comune: {ind.get('descComune')}")
        print(f"  Provincia: {ind.get('descProvincia')}")
        print(f"  Prezzo: {body.get('impoBaseAsta')}")
        print(f"  Tipo vendita: {body.get('descTipoVendita')}")
        print(f"\nAllegati trovati: {len(docs)}")

        await browser.close()

asyncio.run(test())
