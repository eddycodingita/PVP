"""
#3 — Scarica dettagli + allegati PDF per ogni asta.

Strategia definitiva (testata e funzionante):
1. Naviga la pagina dettaglio con Playwright
2. Intercetta l'API /ve-ms/vendite/{id}/restricted per i dati strutturati
3. Aspetta il rendering Angular (sezione Allegati)
4. Clicca ogni bottone "Scarica documento" e intercetta l'URL del download
5. Salva URL allegati in Supabase (tabella documenti)
6. Aggiorna l'asta con i dati del dettaglio

Uso standalone:
    python scraper/document_downloader.py --limit 50
    python scraper/document_downloader.py --limit 500 --concurrency 2
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, logging, argparse
from datetime import datetime, timezone
from playwright.async_api import async_playwright, BrowserContext
from db.client import get_supabase, db_insert_documento

log = logging.getLogger("downloader")

BASE_URL    = "https://pvp.giustizia.it"
DETAIL_API  = "ve-3f723b85-986a1b71/ve-ms/vendite/{pvp_id}/restricted"
DELAY       = 1.5    # secondi tra un'asta e l'altra
CONCURRENCY = 2      # browser tabs in parallelo (basso per non sovraccaricare)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"


class DocumentDownloader:
    def __init__(self, limit: int = 50, concurrency: int = CONCURRENCY):
        self.limit       = limit
        self.concurrency = concurrency
        self.stats       = {"processed": 0, "docs_saved": 0, "errors": 0, "skipped": 0}

    async def run(self) -> dict:
        sb = get_supabase()

        # Prendi aste senza dettaglio (mq=null come proxy)
        res = (
            sb.table("aste")
            .select("id,pvp_id,url_dettaglio,data_pubblicazione")
            .eq("is_active", True)
            .is_("mq", "null")
            .order("data_pubblicazione", desc=True)
            .limit(self.limit)
            .execute()
        )
        aste = res.data or []
        log.info(f"Aste da processare: {len(aste)}")

        if not aste:
            log.info("Nessuna asta da processare")
            return self.stats

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = await browser.new_context(
                user_agent=USER_AGENT,
                locale="it-IT",
                accept_downloads=True,
            )
            try:
                sem = asyncio.Semaphore(self.concurrency)
                await asyncio.gather(
                    *[self._process(ctx, sem, a) for a in aste],
                    return_exceptions=True,
                )
            finally:
                await browser.close()

        log.info(
            f"Completato — processed={self.stats['processed']} "
            f"docs={self.stats['docs_saved']} errors={self.stats['errors']}"
        )
        return self.stats

    async def _process(self, ctx: BrowserContext, sem: asyncio.Semaphore, asta: dict):
        async with sem:
            pvp_id = asta["pvp_id"]
            url    = asta["url_dettaglio"]
            db_id  = asta["id"]

            try:
                await asyncio.sleep(DELAY)
                result = await self._scrape(ctx, url, pvp_id)

                if not result:
                    self.stats["errors"] += 1
                    return

                # Aggiorna asta con dati dettaglio
                update = {k: v for k, v in result["asta"].items() if v is not None}
                if update:
                    get_supabase().table("aste").update(update).eq("id", db_id).execute()

                # Salva documenti
                docs_saved = 0
                for doc in result["documenti"]:
                    try:
                        db_insert_documento(db_id, doc)
                        docs_saved += 1
                    except Exception:
                        pass

                self.stats["processed"]  += 1
                self.stats["docs_saved"] += docs_saved
                log.info(
                    f"  ✓ {pvp_id} | {result['asta'].get('comune','?')} "
                    f"| mq={result['asta'].get('mq','?')} "
                    f"| {docs_saved} allegati"
                )

            except Exception as e:
                log.error(f"  ✗ {pvp_id}: {e}")
                self.stats["errors"] += 1

    async def _scrape(self, ctx: BrowserContext, url: str, pvp_id: str) -> dict | None:
        """
        Naviga la pagina dettaglio, intercetta l'API e i download PDF.
        """
        page          = await ctx.new_page()
        api_data      = {}
        api_responses = {}  # tutte le risposte API (per trovare allegati)
        api_event     = asyncio.Event()

        async def on_response(response):
            url_r = response.url
            # Risposta principale dettaglio
            if f"vendite/{pvp_id}/restricted" in url_r:
                try:
                    data = await response.json()
                    api_data.update(data)
                    api_responses[url_r] = data
                    api_event.set()
                except Exception:
                    api_event.set()
            # Altre risposte API (lotti, allegati, ecc.)
            elif any(x in url_r for x in ["ve-ms/", "allegati", "documenti"]):
                try:
                    data = await response.json()
                    api_responses[url_r] = data
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=40000)

            # Aspetta API dettaglio
            try:
                await asyncio.wait_for(api_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                log.warning(f"  Timeout API per {pvp_id}")

            # Intercetta allegati dalla risposta API
            import urllib.parse
            documenti = []
            seen_urls = set()

            for url_resp, data_resp in api_responses.items():
                body_resp = data_resp.get("body") or data_resp
                if not isinstance(body_resp, dict):
                     continue
                # Cerca allegati in tutti i posti possibili
                allegati_list = []
                allegati_list += body_resp.get("allegati") or []
                allegati_list += (body_resp.get("lotto") or {}).get("allegati") or []
                for bene in (body_resp.get("beni") or []):
                    allegati_list += bene.get("allegati") or []

                for a in allegati_list:
                    nome = (a.get("nomeFile") or a.get("nome") or
                            a.get("fileName") or "allegato.pdf")
                    # Usa linkAllegato direttamente se disponibile
                    link = a.get("linkAllegato")
                    if link:
                        durl = f"https://resource-pvp.giustizia.it{link}" if link.startswith("/") else link
                    else:
                        durl = (a.get("url") or a.get("urlDownload") or a.get("link"))
                        if not durl:
                            fn = a.get("nomeFile") or a.get("fileName")
                            if fn:
                                durl = f"https://resource-pvp.giustizia.it/allegati/{pvp_id}/{urllib.parse.quote(fn)}"
                    if durl and durl not in seen_urls:
                        seen_urls.add(durl)
                        documenti.append({
                            "nome":          str(nome)[:200],
                            "url_originale": durl,
                            "tipo":          _classify_doc(str(nome)),
                            "scaricato":     False,
                        })

            # Fallback: clicca bottoni se API non ha restituito allegati
            if not documenti:
                try:
                    await page.wait_for_selector("button:has-text('Scarica')", timeout=8000)
                    await asyncio.sleep(2)
                except Exception:
                    pass

                buttons       = await page.query_selector_all("button:has-text('Scarica')")
                pdf_urls_queue = []

                async def capture_pdf_url(request):
                    u = request.url
                    if "resource-pvp" in u or "/allegati/" in u:
                        pdf_urls_queue.append(u)

                page.on("request", capture_pdf_url)

                for btn in buttons:
                    pdf_urls_queue.clear()
                    try:
                        async with page.expect_download(timeout=8000) as dl_info:
                            await btn.click()
                        dl   = await dl_info.value
                        nome = dl.suggested_filename or f"allegato_{pvp_id}.pdf"
                        durl = pdf_urls_queue[0] if pdf_urls_queue else None
                        await dl.cancel()
                        if durl:
                            documenti.append({
                                "nome":          nome[:200],
                                "url_originale": durl,
                                "tipo":          _classify_doc(nome),
                                "scaricato":     False,
                            })
                    except Exception as e:
                        log.debug(f"  Download btn error {pvp_id}: {e}")

                page.remove_listener("request", capture_pdf_url)
            log.debug(f"  Allegati trovati: {len(documenti)} per {pvp_id}")

            # Parsea dati strutturati dall'API
            asta_update = _parse_api(api_data)

            return {"asta": asta_update, "documenti": documenti}

        except Exception as e:
            log.error(f"  Errore scraping {url}: {e}")
            return None
        finally:
            await page.close()


def _to_iso_date(s: str | None) -> str | None:
    """Converte data italiana 'DD/MM/YYYY' in ISO 'YYYY-MM-DD'."""
    if not s:
        return None
    import re
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(s))
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return s  # già in formato corretto


def _parse_api(data: dict) -> dict:
    """Estrae i campi utili dalla risposta /restricted."""
    d = {}
    if not data:
        return d

    # Il body può essere direttamente nella risposta o in data["body"]
    body = data.get("body") or data

    # Dati vendita — converti date in formato ISO
    d["tipo_vendita"]     = body.get("descTipoVendita")
    d["modalita_vendita"] = body.get("descModVendita")
    d["data_scadenza"]    = _to_iso_date(body.get("dataTermPresOff"))
    d["prezzo_base"]      = body.get("impoBaseAsta")
    d["offerta_minima"]   = body.get("impoOffertaMinima")
    d["rialzo_minimo"]    = body.get("impoOffertaAumento")

    # Dati lotto
    lotto = body.get("lotto") or {}
    if lotto:
        ind = lotto.get("indirizzo") or {}
        d["comune"]     = ind.get("descComune")
        d["provincia"]  = ind.get("descProvincia")
        d["regione"]    = ind.get("descRegione")
        d["latitudine"] = (ind.get("coordinate") or {}).get("latitudine")
        d["longitudine"]= (ind.get("coordinate") or {}).get("longitudine")
        d["tipologia"]  = lotto.get("codTipoCategLotto")
        d["descrizione"]= lotto.get("descLotto")
        # mq dal lotto se disponibile
        beni = lotto.get("beni") or []
        for bene in beni:
            if bene.get("superficie"):
                d["mq"] = bene["superficie"]
                break

    # Se mq non trovato nei beni, metti un valore placeholder per non
    # riprocessare questa asta all'infinito
    if not d.get("mq"):
        d["mq"] = -1  # indica "processato ma mq non disponibile"

    # Tipo procedura (fallimento, pignoramento, ecc.)
    procedura = body.get("procedura") or {}
    if procedura:
        d["tipo_procedura"] = procedura.get("descTipoRito")
        d["anno_procedura"] = procedura.get("numeAnnoRg")
        # numero_procedura dall'anno + numero RG
        nr = procedura.get("numeRg")
        anno = procedura.get("numeAnnoRg")
        if nr and anno:
            d["numero_procedura"] = f"{nr}/{anno}"

    # Referenti (soggetti nel JSON)
    referenti = body.get("soggetti") or body.get("referenti") or []
    for r in referenti:
        ruolo = str(r.get("ruolo") or r.get("tipoSoggetto") or "").upper()
        nome  = f"{r.get('nome','').strip()} {r.get('cognome','').strip()}".strip()
        if not nome:
            nome = r.get("denominazione") or r.get("ragioneSociale") or ""
        if nome:
            if "GIUDICE"  in ruolo: d["giudice"]  = nome
            if "DELEGATO" in ruolo: d["delegato"] = nome
            if "CUSTODE"  in ruolo:
                d["custode"] = nome
                # estrai email e telefono custode
                email = r.get("email")
                tel   = r.get("telefono")
                if email: d["custode_email"] = email
                if tel:   d["custode_tel"]   = tel
            if "CURATORE" in ruolo: d["delegato"] = d.get("delegato") or nome

    return {k: v for k, v in d.items() if v is not None}


def _classify_doc(s: str) -> str:
    s = s.lower()
    if "perizia"     in s: return "perizia"
    if "avviso"      in s: return "avviso_vendita"
    if "planimetri"  in s: return "planimetria"
    if "relazione"   in s: return "relazione"
    if "ordinanza"   in s: return "ordinanza"
    if "onorari"     in s: return "onorari"
    if "regolamento" in s: return "regolamento"
    if "modello"     in s: return "avviso_vendita"
    return "allegato"


# ── Entry point standalone ────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Scarica dettagli e allegati aste PVP")
    parser.add_argument("--limit",       type=int, default=50,
                        help="N. max aste da processare (default: 50)")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Tab paralleli (default: 2)")
    args = parser.parse_args()

    async def main():
        d = DocumentDownloader(limit=args.limit, concurrency=args.concurrency)
        s = await d.run()
        print(f"\nRisultato finale: {s}")

    asyncio.run(main())
