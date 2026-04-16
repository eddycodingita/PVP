"""
Scraper dettaglio asta PVP via Playwright.
Intercetta ve-ms/vendite/{id}/restricted e parsed i campi reali.

Uso:
    python scraper_detail.py
"""
import asyncio
import json
from playwright.async_api import async_playwright

# ── CONFIGURAZIONE ────────────────────────────────────────────────────────────
TEST_IDS       = [1738450, 4561359]
HEADLESS       = False
TIMEOUT        = 60_000
ALLEGATI_BASE  = "https://resource-pvp.giustizia.it"


def build_url(pvp_id: int) -> str:
    return f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={pvp_id}"


def parse_detail(data: dict) -> dict:
    """Mappa i campi reali di ve-ms/vendite/{id}/restricted."""
    body = data.get("body", data)

    # ── Procedura / Tribunale ────────────────────────────────────────────────
    proc = body.get("procedura") or {}
    tribunale       = proc.get("descUfficio")
    numero_proc     = proc.get("numeRg")
    anno_proc       = proc.get("numeAnnoRg")
    tipo_rito       = proc.get("descTipoRito")

    # ── Lotto principale ─────────────────────────────────────────────────────
    lotto     = body.get("lotto") or {}
    lotto_ind = lotto.get("indirizzo") or {}
    lotto_coord = lotto_ind.get("coordinate") or {}

    # ── Allegati ─────────────────────────────────────────────────────────────
    allegati = []
    for a in body.get("allegati") or []:
        link = a.get("linkAllegato") or ""
        # Il link è relativo tipo "/allegati/4561359/file.pdf?..."
        url_completo = ALLEGATI_BASE + link if link.startswith("/") else link
        allegati.append({
            "id":          a.get("idAllegato"),
            "nome":        a.get("nomeFile"),
            "tipo":        a.get("descrizione"),
            "codice_tipo": a.get("codiceTipoAllegato"),  # PERIZ, AVEND, ORDIN, ALTRO
            "dimensione":  a.get("dimensioneAllegato"),
            "url":         url_completo,
        })

    # ── Beni ─────────────────────────────────────────────────────────────────
    beni = []
    for b in body.get("beni") or []:
        ind = b.get("indirizzo") or {}
        coord = ind.get("coordinate") or {}
        beni.append({
            "id":           b.get("idBene"),
            "tipologia":    b.get("descTipologiaBene"),
            "categoria":    b.get("descTipoCategLotto"),
            "descrizione":  b.get("descrizione"),
            "indirizzo":    ind.get("via"),
            "comune":       ind.get("descComune"),
            "provincia":    ind.get("descProvincia"),
            "regione":      ind.get("descRegione"),
            "cod_provincia":ind.get("codProvincia"),
            "latitudine":   coord.get("latitudine"),
            "longitudine":  coord.get("longitudine"),
            "superficie":   b.get("superficie"),
            "vani":         b.get("numeroVani"),
            "piano":        b.get("piano"),
            "disponibilita":b.get("disponibilitaDesc"),
        })

    # ── Soggetti (delegato, custode, giudice) ────────────────────────────────
    soggetti = []
    for s in body.get("soggetti") or []:
        soggetti.append({
            "ruolo":    s.get("ruolo"),
            "nome":     s.get("nome"),
            "cognome":  s.get("cognome"),
            "email":    s.get("email"),
            "telefono": s.get("telefono"),
        })

    return {
        # Identificativi
        "pvp_id":           body.get("idVendita"),
        # Vendita
        "tipo_vendita":     body.get("descTipoVendita"),
        "modalita":         body.get("descModVendita"),
        "data_vendita":     body.get("dataVendita"),
        "ora_vendita":      body.get("oraVendita"),
        "termine_offerte":  body.get("dataTermPresOff"),
        "data_pubblicazione": body.get("dataDiPubblicazione"),
        # Prezzi
        "prezzo_base":      body.get("impoBaseAsta"),
        "offerta_minima":   body.get("impoOffertaMinima"),
        "rialzo_minimo":    body.get("impoOffertaAumento"),
        # Procedura
        "tribunale":        tribunale,
        "numero_procedura": numero_proc,
        "anno_procedura":   anno_proc,
        "tipo_rito":        tipo_rito,
        # Lotto
        "lotto_codice":     lotto.get("codLotto"),
        "lotto_categoria":  lotto.get("descTipoCategLotto"),
        "lotto_descrizione":lotto.get("descLotto"),
        "comune":           lotto_ind.get("descComune"),
        "provincia":        lotto_ind.get("descProvincia"),
        "regione":          lotto_ind.get("descRegione"),
        "indirizzo":        lotto_ind.get("via"),
        "latitudine":       lotto_coord.get("latitudine"),
        "longitudine":      lotto_coord.get("longitudine"),
        # Allegati e beni
        "allegati":         allegati,
        "beni":             beni,
        "soggetti":         soggetti,
    }


async def fetch_detail(page, pvp_id: int) -> dict | None:
    """Carica la pagina e intercetta la risposta API."""
    captured = {}

    async def on_response(response):
        if (f"ve-ms/vendite/{pvp_id}/restricted" in response.url
                and response.status == 200):
            try:
                captured["data"] = await response.json()
                print(f"  [OK] {response.url}")
            except Exception as e:
                print(f"  [ERR parse] {e}")

    page.on("response", on_response)
    try:
        await page.goto(build_url(pvp_id), wait_until="networkidle", timeout=TIMEOUT)
        await asyncio.sleep(8)
    except Exception as e:
        print(f"  [ERR goto] {e}")

    page.remove_listener("response", on_response)

    if "data" not in captured:
        print(f"  [MISS] Nessuna risposta per ID {pvp_id}")
        return None

    return parse_detail(captured["data"])


async def main():
    print("=" * 70)
    print("SCRAPER DETTAGLIO PVP")
    print("=" * 70)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()

        for pvp_id in TEST_IDS:
            print(f"\n--- ID {pvp_id} ---")
            detail = await fetch_detail(page, pvp_id)
            if not detail:
                continue

            results.append(detail)
            print(f"  tribunale:    {detail['tribunale']}")
            print(f"  tipo_rito:    {detail['tipo_rito']}")
            print(f"  data_vendita: {detail['data_vendita']}")
            print(f"  prezzo_base:  {detail['prezzo_base']:,} €")
            print(f"  comune:       {detail['comune']} ({detail['provincia']})")
            print(f"  lotto:        {detail['lotto_codice']} — {detail['lotto_categoria']}")
            print(f"  allegati ({len(detail['allegati'])}):")
            for a in detail["allegati"]:
                print(f"    [{a['codice_tipo']}] {a['nome']}")
                print(f"           → {a['url']}")
            print(f"  beni ({len(detail['beni'])}):")
            for b in detail["beni"]:
                print(f"    {b['tipologia']} | {b['comune']} ({b['provincia']}) | {b['superficie']} mq | {b['disponibilita']}")

        await browser.close()

    with open("details_scraped.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print(f"Salvati {len(results)} dettagli in details_scraped.json")
    print("=" * 70)


asyncio.run(main())