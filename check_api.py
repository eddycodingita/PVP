import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # visibile per debug
        ctx     = await browser.new_context()
        page    = await ctx.new_page()

        # Intercetta le risposte API
        api_results = []

        async def on_response(response):
            if "ricerca/vendite" in response.url:
                try:
                    data    = await response.json()
                    content = data.get("body", {}).get("content", [])
                    ids     = [str(i["id"]) for i in content]
                    api_results.append(ids)
                    print(f"Intercettata pagina {len(api_results)}: {ids[:3]}")
                except Exception as e:
                    print(f"Errore parsing: {e}")

        page.on("response", on_response)

        # Naviga sul sito e lascia che il browser carichi le aste
        await page.goto("https://pvp.giustizia.it/pvp/it/lista_annunci.page?searchType=searchForm&page=0&size=20&codTipoLotto=IMMOBILI&nazione=Italia")
        await asyncio.sleep(5)

        # Vai alla pagina 2
        await page.goto("https://pvp.giustizia.it/pvp/it/lista_annunci.page?searchType=searchForm&page=1&size=20&codTipoLotto=IMMOBILI&nazione=Italia")
        await asyncio.sleep(5)

        # Vai alla pagina 3
        await page.goto("https://pvp.giustizia.it/pvp/it/lista_annunci.page?searchType=searchForm&page=2&size=20&codTipoLotto=IMMOBILI&nazione=Italia")
        await asyncio.sleep(5)

        all_ids = set()
        for r in api_results:
            all_ids.update(r)

        print(f"\nRisultati intercettati: {len(api_results)} pagine")
        print(f"ID unici totali: {len(all_ids)}")

        await browser.close()

asyncio.run(test())