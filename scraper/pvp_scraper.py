"""
Scraper PVP — strategia ibrida:
- Lista annunci via API JSON interna (veloce)
- Dettaglio + allegati via Playwright (necessario)
"""
import asyncio, json, logging, re, httpx
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin
from playwright.async_api import async_playwright, BrowserContext
from db.client import db_asta_exists, db_upsert_asta, db_insert_documento
from utils.pvp_http import playwright_get_with_retry, is_block_response

log = logging.getLogger("scraper")

BASE_URL    = "https://pvp.giustizia.it"
API_URL     = "https://pvp.giustizia.it/ric-496b258c-986a1b71/ric-ms/ricerca/vendite"
PAGE_SIZE   = 50
CONCURRENCY = 3
DELAY       = 0.5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
]


class PVPScraper:
    def __init__(self, only_today: bool = True):
        self.only_today = only_today
        self.today = date.today().isoformat()
        self.stats = {"scraped": 0, "new": 0, "skipped": 0, "errors": 0}

    async def run(self) -> dict:
        # Step 1: lista via API JSON
        stubs = await self._fetch_list_api()
        log.info(f"Annunci trovati via API: {len(stubs)}")

        if not stubs:
            return self.stats

        # Step 2: dettagli via Playwright
        async with async_playwright() as p:
            import random
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                locale="it-IT",
            )
            try:
                sem = asyncio.Semaphore(CONCURRENCY)
                await asyncio.gather(
                    *[self._process(ctx, sem, s) for s in stubs],
                    return_exceptions=True,
                )
            finally:
                await browser.close()

        return self.stats

    async def _fetch_list_api(self) -> list[dict]:
        """Scarica la lista annunci via API JSON — veloce, nessun browser."""
        stubs = []
        page  = 0

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                payload = {
                    "language": "it",
                    "page": page,
                    "size": PAGE_SIZE,
                    "sortProperty": "dataPubblicazione",
                    "sortDirection": "DESC",
                    "codTipoLotto": "IMMOBILI",
                    "nazione": "Italia",
                }
                try:
                    r = await client.post(
                        API_URL,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Accept":       "application/json",
                            "User-Agent":   "Mozilla/5.0",
                        },
                    )
                    r.raise_for_status()
                    data    = r.json()
                    body    = data.get("body") or {}
                    content = body.get("content") or []

                    if not content:
                        break

                    for item in content:
                        pub_date = item.get("dataPubblicazione", "")

                        # Modalità daily: fermati sulle aste di giorni precedenti
                        if self.only_today and pub_date and pub_date < self.today:
                            log.info(f"  Data {pub_date} < oggi {self.today} — stop")
                            return stubs

                        # Gestione sicura dell'indirizzo (può essere None)
                        indirizzo  = item.get("indirizzo") or {}
                        coordinate = indirizzo.get("coordinate") or {}

                        stubs.append({
                            "pvp_id":             str(item["id"]),
                            "url_dettaglio":      f"{BASE_URL}/pvp/it/detail_annuncio.page?idAnnuncio={item['id']}",
                            "data_pubblicazione": pub_date,
                            "tribunale":          item.get("tribunale"),
                            "numero_procedura":   item.get("procedura"),
                            "lotto":              item.get("numeroLotto"),
                            "tipologia":          item.get("categoriaLotto"),
                            "indirizzo":          indirizzo.get("via"),
                            "comune":             indirizzo.get("citta"),
                            "provincia":          indirizzo.get("provincia"),
                            "latitudine":         coordinate.get("latitudine"),
                            "longitudine":        coordinate.get("longitudine"),
                            "prezzo_base":        item.get("prezzoBaseAsta"),
                            "offerta_minima":     item.get("offertaMinima"),
                            "rialzo_minimo":      item.get("rialzoMinimo"),
                            "data_vendita":       item.get("dataVendita"),
                            "descrizione":        item.get("descLotto"),
                            "occupazione":        _parse_disponibilita(item.get("disponibilita") or []),
                        })

                    # FIX: usa totalPages e il flag "last" invece di totalElements
                    total_pages = body.get("totalPages", 1)
                    is_last     = body.get("last", True)

                    log.info(f"  Pagina {page + 1}/{total_pages} — {len(content)} annunci")

                    if is_last or page + 1 >= total_pages:
                        break

                    page += 1
                    await asyncio.sleep(0.5)

                except Exception as e:
                    log.error(f"Errore API pagina {page}: {e}")
                    break

        return stubs

    async def _process(self, ctx: BrowserContext, sem: asyncio.Semaphore, stub: dict):
        async with sem:
            pvp_id = stub["pvp_id"]
            url    = stub["url_dettaglio"]

            if db_asta_exists(pvp_id):
                self.stats["skipped"] += 1
                return

            try:
                await asyncio.sleep(DELAY)
                html = await playwright_get_with_retry(ctx, url)

                if not html or is_block_response(html):
                    self.stats["errors"] += 1
                    return

                # Arricchisci con dati dal dettaglio HTML
                detail = _parse_detail_html(html, url)
                # Merge: i dati API hanno precedenza per i campi base
                stub.update({k: v for k, v in detail.items() if v and not stub.get(k)})
                stub["pvp_id"] = pvp_id

                asta_db_id = db_upsert_asta(stub)
                for doc in detail.get("documenti", []):
                    db_insert_documento(asta_db_id, doc)

                self.stats["scraped"] += 1
                self.stats["new"]     += 1
                log.info(
                    f"  ✓ {pvp_id} | {stub.get('comune')} | "
                    f"€{stub.get('prezzo_base')} | "
                    f"{len(detail.get('documenti', []))} doc"
                )

            except Exception as e:
                log.error(f"  ✗ {pvp_id}: {e}")
                self.stats["errors"] += 1


def _parse_disponibilita(disp: list) -> Optional[str]:
    if not disp:
        return None
    vals = [str(d).upper() for d in disp]
    if all("LIBER" in v for v in vals):
        return "Libero"
    if any("OCCUP" in v for v in vals):
        return "Occupato"
    return "Parzialmente libero"


def _parse_detail_html(html: str, url: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    d    = {"documenti": []}

    # Allegati PDF
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href:
            continue
        if ".pdf" in href.lower() or "scarica" in href.lower() or "allegat" in href.lower():
            full_url = urljoin(BASE_URL, href)
            if full_url not in seen:
                seen.add(full_url)
                nome = a.get_text(strip=True) or href.split("/")[-1]
                d["documenti"].append({
                    "nome": nome[:200],
                    "url":  full_url,
                    "tipo": _classify_doc(nome + href),
                })

    # Campi aggiuntivi dal dettaglio HTML
    def val(label):
        for dt in soup.find_all("dt"):
            if re.search(label, dt.get_text(), re.I):
                dd = dt.find_next_sibling("dd")
                if dd:
                    return dd.get_text(strip=True)
        return None

    d["tipo_vendita"]     = val(r"Tipologia.*vendita|Tipo.*vendita")
    d["modalita_vendita"] = val(r"Modalit")
    d["data_scadenza"]    = val(r"Termine.*offert|Scadenza")
    d["giudice"]          = val(r"Giudice")
    d["delegato"]         = val(r"Delegato|Curatore|Soggetto specializzato")
    d["custode"]          = val(r"Custode")

    return d


def _classify_doc(s: str) -> str:
    s = s.lower()
    if "perizia"    in s: return "perizia"
    if "avviso"     in s: return "avviso_vendita"
    if "planimetri" in s: return "planimetria"
    if "relazione"  in s: return "relazione"
    if "ordinanza"  in s: return "ordinanza"
    return "allegato"