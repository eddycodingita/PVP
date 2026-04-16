"""
Clicca i bottoni 'Scarica documento' e intercetta le richieste di download.
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
        browser = await p.chromium.launch(headless=False)
        ctx     = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            locale="it-IT",
            accept_downloads=True,
        )
        page = await ctx.new_page()

        # Intercetta tutte le richieste di rete
        pdf_requests = []

        async def on_request(request):
            url = request.url
            if any(x in url.lower() for x in ["pdf", "allegat", "document", "download", "file", "scaric"]):
                print(f"REQUEST: {url}")
                pdf_requests.append(url)

        async def on_response(response):
            url = response.url
            ct  = response.headers.get("content-type", "")
            if "pdf" in ct or "octet" in ct or any(x in url.lower() for x in ["pdf", "allegat", "document"]):
                print(f"RESPONSE PDF: {url} | {ct}")
                pdf_requests.append(url)

        page.on("request",  on_request)
        page.on("response", on_response)

        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector("text=Allegati", timeout=15000)
        await asyncio.sleep(3)

        # Trova tutti i bottoni Scarica
        buttons = await page.query_selector_all("button:has-text('Scarica')")
        print(f"\nBottoni 'Scarica' trovati: {len(buttons)}")

        # Trova anche i nomi dei documenti vicino ai bottoni
        nomi = await page.evaluate("""
            () => {
                const items = [];
                document.querySelectorAll('button').forEach(btn => {
                    if (btn.textContent.includes('Scarica')) {
                        // Cerca il nome del documento nel contenitore padre
                        const container = btn.closest('[class*="allegat"], [class*="file"], div');
                        const text = container ? container.textContent.trim().replace(/\\s+/g, ' ') : '';
                        items.push(text.substring(0, 200));
                    }
                });
                return items;
            }
        """)
        print("\nNomi documenti trovati:")
        for n in nomi:
            print(f"  - {n[:100]}")

        # Clicca ogni bottone e intercetta il download
        print(f"\nClicco {len(buttons)} bottoni...")
        for i, btn in enumerate(buttons):
            print(f"\n--- Bottone {i+1} ---")
            try:
                async with page.expect_download(timeout=10000) as dl_info:
                    await btn.click()
                download = await dl_info.value
                print(f"  Download: {download.suggested_filename}")
                print(f"  URL: {download.url}")
                pdf_requests.append(download.url)
            except Exception as e:
                print(f"  Nessun download diretto: {e}")
                # Aspetta richiesta di rete
                await asyncio.sleep(2)

        print(f"\n--- RIEPILOGO PDF ---")
        for url in set(pdf_requests):
            print(f"  {url}")

        input("\nPremi INVIO per chiudere...")
        await browser.close()

asyncio.run(test())
