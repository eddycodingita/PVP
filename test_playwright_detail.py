"""
Test scraping dettaglio asta PVP con Playwright.
Usa un profilo temporaneo — Chrome non deve essere chiuso.

Uso:
    python test_playwright_detail.py
"""
import asyncio
import json
from playwright.async_api import async_playwright

TEST_ID = 1738450
URL = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={TEST_ID}"


async def main():
    print("=" * 70)
    print("TEST PLAYWRIGHT — DETTAGLIO ASTA PVP")
    print("=" * 70)
    print(f"\nURL: {URL}\n")

    async with async_playwright() as p:

        # Browser visibile, profilo fresco — nessun conflitto con Chrome aperto
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],
        )

        context = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="it-IT",
            extra_http_headers={
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        page = await context.new_page()

        # Rimuovi il webdriver flag (anti-bot)
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
        """)

        # Intercetta le chiamate API
        api_responses = []

        async def handle_response(response):
            url = response.url
            if "pvp.giustizia.it" in url and response.status == 200:
                ctype = response.headers.get("content-type", "")
                if "json" in ctype:
                    try:
                        body = await response.json()
                        api_responses.append({"url": url, "data": body})
                        print(f"  [API OK] {url}")
                    except Exception:
                        pass

        page.on("response", handle_response)

        print("Apro la pagina (browser visibile)...")
        await page.goto(URL, wait_until="networkidle", timeout=60000)

        print("Aspetto caricamento Angular (12s)...")
        await asyncio.sleep(12)

        title = await page.title()
        body_text = await page.evaluate("() => document.body.innerText")

        if "blocked" in body_text.lower():
            print(f"\nERRORE WAF: {body_text[:300]}")
            await browser.close()
            return

        print(f"Pagina: {title}")

        # ── TESTO ──────────────────────────────────────────────────────────
        print("\n--- TESTO PAGINA (primi 3000 car.) ---")
        print(body_text[:3000])

        # ── DOCUMENTI ──────────────────────────────────────────────────────
        print("\n--- LINK DOCUMENTI ---")
        links = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('a'))
                .map(a => ({ text: a.innerText.trim(), href: a.href }))
                .filter(a => a.href && a.href.length > 10 && (
                    a.href.includes('.pdf') ||
                    a.href.includes('document') ||
                    a.href.includes('allegat') ||
                    a.href.includes('download') ||
                    a.href.includes('perizia') ||
                    a.href.includes('ordinanza') ||
                    a.href.includes('avviso') ||
                    a.href.includes('file')
                ))
        """)
        if links:
            for l in links:
                print(f"  {l['text'][:50]:<50} -> {l['href']}")
        else:
            print("  Nessuno")

        # ── IMMAGINI ───────────────────────────────────────────────────────
        print("\n--- IMMAGINI ---")
        images = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('img'))
                .map(i => i.src)
                .filter(s => s && !s.includes('logo') &&
                             !s.includes('icon') && !s.includes('favicon'))
        """)
        if images:
            for src in images[:10]:
                print(f"  {src[:100]}")
        else:
            print("  Nessuna")

        # ── API ────────────────────────────────────────────────────────────
        print("\n--- API INTERCETTATE ---")
        if api_responses:
            for r in api_responses:
                print(f"\n  {r['url']}")
                print(f"  {json.dumps(r['data'], ensure_ascii=False)[:400]}")
            with open("detail_api_intercepted.json", "w", encoding="utf-8") as f:
                json.dump(api_responses, f, indent=2, ensure_ascii=False)
            print("\n  -> Salvato in detail_api_intercepted.json")
        else:
            print("  Nessuna chiamata API JSON intercettata")

        await page.screenshot(path="detail_screenshot.png", full_page=True)
        print("\nScreenshot: detail_screenshot.png")

        await browser.close()

    print("\n" + "=" * 70)
    print("FATTO!")
    print("=" * 70)


asyncio.run(main())