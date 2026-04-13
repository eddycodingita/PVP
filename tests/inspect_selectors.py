"""
SELECTOR INSPECTOR — Esegui questo script PRIMA di tutto, sulla tua macchina.

Apre il PVP con un browser visibile, intercetta tutte le chiamate di rete
e analizza la struttura DOM per trovare i selettori CSS reali.

In base all'output, aggiorna pvp_scraper.py → _parse_list_html()

Uso:
    python tests/inspect_selectors.py
"""
import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL = (
    "https://pvp.giustizia.it/pvp/it/lista_annunci.page"
    "?searchType=searchForm&page=0&size=12"
    "&sortProperty=dataPubblicazione,desc&sortAlpha=citta,asc"
    "&searchWith=Ricerca%20Geografica&codTipoLotto=IMMOBILI"
    "&raggioAzione=25&nazione=Italia"
)

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # VISIBILE!
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            locale="it-IT",
        )
        page = await ctx.new_page()

        xhr = []
        async def on_resp(r):
            if r.request.resource_type in ("xhr","fetch"):
                try:
                    body = await r.body()
                    text = body.decode("utf-8", errors="ignore")
                    xhr.append({"url": r.url, "status": r.status,
                                "ct": r.headers.get("content-type",""),
                                "preview": text[:600]})
                except: pass
        page.on("response", on_resp)

        print(f"Navigating to PVP...")
        await page.goto(URL, wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(3)

        html = await page.content()
        Path("snapshot_lista.html").write_text(html, encoding="utf-8")
        print(f"✓ snapshot_lista.html salvato ({len(html):,} chars)\n")

        # ── XHR / FETCH JSON ─────────────────────────────────────────────
        print("=" * 60)
        print(f"CHIAMATE XHR/FETCH: {len(xhr)}")
        print("=" * 60)
        for c in xhr:
            print(f"\n  {c['status']} {c['url'][:100]}")
            print(f"  Content-Type: {c['ct']}")
            if "json" in c["ct"].lower():
                print(f"  *** JSON RESPONSE! ***")
                print(f"  {c['preview'][:400]}")
                Path("snapshot_api.json").write_text(c["preview"], encoding="utf-8")
                print("  → Salvato snapshot_api.json")

        # ── ANALISI DOM ───────────────────────────────────────────────────
        print(f"\n{'=' * 60}")
        print("SELETTORI RILEVANTI")
        print("=" * 60)

        candidates = [
            ".annuncio-item", ".annuncio-card", ".card-annuncio",
            ".lotto-card", ".lotto-item", "[data-lotto-id]", "[data-id]",
            "article", ".risultato", ".card", "[class*='annuncio']",
            "[class*='lotto']", "[class*='result']", "[class*='card']",
        ]
        for sel in candidates:
            count = await page.locator(sel).count()
            if count > 0:
                print(f"\n  ✓ '{sel}' → {count} elementi")
                first = page.locator(sel).first
                cls = await first.get_attribute("class") or ""
                print(f"    class: '{cls}'")
                # Link nel primo elemento
                link = first.locator("a").first
                if await link.count() > 0:
                    href = await link.get_attribute("href")
                    print(f"    primo link: {href}")
                # Testo del primo elemento
                text = await first.inner_text()
                print(f"    testo: {text[:100]!r}")

        # ── PAGINAZIONE ───────────────────────────────────────────────────
        print(f"\n{'=' * 60}")
        print("PAGINAZIONE E TOTALE")
        print("=" * 60)
        for sel in [".pagination", "[class*='paginat']", "[class*='page']"]:
            n = await page.locator(sel).count()
            if n: print(f"  '{sel}' → {n}")

        # Cerca elemento con numero totale risultati
        for sel in ["h1","h2","h3","h4","p","span","div"]:
            els = page.locator(sel)
            for i in range(min(await els.count(), 30)):
                t = await els.nth(i).inner_text()
                if re.search(r'\d{3,}', t) and len(t) < 80:
                    print(f"  Possibile totale: '{sel}' → {t.strip()!r}")
                    break

        print(f"\n{'=' * 60}")
        print("PROSSIMI PASSI:")
        print("  1. Se hai trovato JSON XHR → il sito ha un'API interna!")
        print("     Aggiorna _parse_list_html() per parsare il JSON direttamente.")
        print("  2. Se hai trovato selettori → aggiornali in _parse_list_html()")
        print("  3. Apri snapshot_lista.html nel browser per ispezione manuale")
        print("=" * 60)

        input("\nPremi INVIO per chiudere...")
        await browser.close()

asyncio.run(inspect())
