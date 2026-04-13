"""
STEP 1-2: Scraping lista annunci + dettaglio di ogni asta.

#1 — Retry con backoff esponenziale su ogni richiesta Playwright.
#2 — Rilevamento blocco IP / captcha: se la pagina segnala un blocco,
     lo scraper si ferma e attende prima di riprovare.
     Se i blocchi si ripetono oltre soglia, la sessione viene riavviata
     con nuovo user-agent e fingerprint casuale.
"""
import asyncio
import json
import logging
import re
import random
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, BrowserContext

from db.client import db_asta_exists, db_upsert_asta, db_insert_documento
from utils.http import playwright_get_with_retry, is_block_response

log = logging.getLogger("scraper")

BASE_URL = "https://pvp.giustizia.it"

LIST_URL = (
    f"{BASE_URL}/pvp/it/lista_annunci.page"
    "?searchType=searchForm"
    "&sortProperty=dataPubblicazione,desc"
    "&sortAlpha=citta,asc"
    "&searchWith=Ricerca%20Geografica"
    "&codTipoLotto=IMMOBILI"
    "&raggioAzione=25"
    "&nazione=Italia"
    "&page={page}&size={size}"
)

PAGE_SIZE    = 20
CONCURRENCY  = 3
DELAY_PAGES  = 2.5    # secondi tra pagine lista
DELAY_DETAIL = 0.7    # secondi tra dettagli
MAX_CONSEC_BLOCKS = 3  # blocchi consecutivi prima di ruotare identità

# Pool di user agent per rotazione (#2)
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


class PVPScraper:
    def __init__(self, only_today: bool = True):
        self.only_today     = only_today
        self.today          = date.today().isoformat()
        self.consec_blocks  = 0   # blocchi consecutivi rilevati
        self.stats = {"scraped": 0, "new": 0, "skipped": 0, "errors": 0, "blocks": 0}

    async def run(self) -> dict:
        async with async_playwright() as p:
            browser, ctx = await self._make_browser(p)
            try:
                await self._scrape_all_pages(ctx, browser, p)
            finally:
                await browser.close()
        return self.stats

    # ── Browser factory ────────────────────────────────────────────────
    async def _make_browser(self, playwright, ua: Optional[str] = None):
        """Crea browser + context con user-agent casuale."""
        agent = ua or random.choice(USER_AGENTS)
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=agent,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366 + random.randint(0, 200),
                      "height": 768 + random.randint(0, 100)},
            # #2 — Maschera il fatto che siamo un browser automatizzato
            extra_http_headers={
                "Accept-Language":  "it-IT,it;q=0.9,en;q=0.8",
                "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest":   "document",
                "Sec-Fetch-Mode":   "navigate",
                "Sec-Fetch-Site":   "none",
            },
        )
        # Inietta script per mascherare WebDriver
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
        """)
        log.info(f"  Browser pronto (UA: {agent[:60]}...)")
        return browser, ctx

    # ── STEP 1: Lista paginata ─────────────────────────────────────────
    async def _scrape_all_pages(self, ctx, browser, playwright):
        page_num    = 0
        total_pages = None

        while True:
            url = LIST_URL.format(page=page_num, size=PAGE_SIZE)
            log.info(f"Lista pagina {page_num + 1}{f'/{total_pages}' if total_pages else ''}")

            html = await playwright_get_with_retry(ctx, url)

            # #2 — Gestione blocco persistente: ruota identità
            if html is None or is_block_response(html):
                self.stats["blocks"] += 1
                self.consec_blocks   += 1
                log.warning(f"  Blocco #{self.consec_blocks} rilevato sulla lista")

                if self.consec_blocks >= MAX_CONSEC_BLOCKS:
                    log.warning("  Troppi blocchi consecutivi — rotazione identità browser")
                    await browser.close()
                    browser, ctx = await self._make_browser(playwright)
                    self.consec_blocks = 0
                    await asyncio.sleep(30)   # pausa lunga dopo rotazione
                    continue
                else:
                    await asyncio.sleep(15)
                    continue

            self.consec_blocks = 0  # reset su successo

            stubs, total = _parse_list_html(html)

            if not stubs:
                log.info("Nessun annuncio trovato — fine lista.")
                break

            if total_pages is None and total:
                total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
                log.info(f"Totale annunci: {total} — Pagine: {total_pages}")

            # ── STEP 2: Dettagli in parallelo ─────────────────────────
            sem     = asyncio.Semaphore(CONCURRENCY)
            results = await asyncio.gather(
                *[self._process_stub(ctx, sem, s) for s in stubs],
                return_exceptions=True,
            )

            old_count = sum(1 for r in results if r == "OLD")
            if self.only_today and old_count == len(stubs):
                log.info("Tutti gli annunci di ieri o prima — stop.")
                break

            page_num += 1
            if total_pages and page_num >= total_pages:
                break

            await asyncio.sleep(DELAY_PAGES)

    # ── STEP 2: Dettaglio singola asta ─────────────────────────────────
    async def _process_stub(self, ctx: BrowserContext, sem: asyncio.Semaphore, stub: dict):
        async with sem:
            url    = stub.get("url_dettaglio")
            pvp_id = stub.get("pvp_id")
            if not url:
                return "SKIP"

            # Modalità daily: salta aste di giorni precedenti
            if self.only_today and stub.get("data_pubblicazione"):
                if stub["data_pubblicazione"][:10] < self.today:
                    self.stats["skipped"] += 1
                    return "OLD"

            # Già in DB
            if pvp_id and db_asta_exists(pvp_id):
                self.stats["skipped"] += 1
                return "KNOWN"

            try:
                await asyncio.sleep(DELAY_DETAIL + random.uniform(0, 0.5))  # jitter umano

                html = await playwright_get_with_retry(ctx, url)

                if html is None:
                    log.warning(f"  Pagina non caricata dopo retry: {url[:80]}")
                    self.stats["errors"] += 1
                    return "ERROR"

                # #2 — Blocco sul dettaglio
                if is_block_response(html):
                    log.warning(f"  Blocco su dettaglio {pvp_id}")
                    self.stats["blocks"] += 1
                    self.stats["errors"] += 1
                    return "BLOCKED"

                detail          = _parse_detail_html(html, url)
                detail["pvp_id"] = pvp_id or detail.get("pvp_id")
                asta_db_id      = db_upsert_asta(detail)

                for doc in detail.get("documenti", []):
                    db_insert_documento(asta_db_id, doc)

                self.stats["scraped"] += 1
                self.stats["new"]     += 1
                log.info(
                    f"  ✓ {pvp_id} | {detail.get('comune')} | "
                    f"€{detail.get('prezzo_base') or '?'} | "
                    f"{len(detail.get('documenti', []))} doc"
                )
                return "OK"

            except Exception as e:
                log.error(f"  ✗ {url[:80]}: {e}")
                self.stats["errors"] += 1
                return "ERROR"


# ── Parsing HTML lista ────────────────────────────────────────────────
def _parse_list_html(html: str) -> tuple[list[dict], int]:
    """
    Estrae stub di annunci dalla pagina lista.
    I selettori vanno verificati con tests/inspect_selectors.py.
    """
    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(html, "html.parser")
    cards = (
        soup.select(".annuncio-item") or soup.select(".card-lotto") or
        soup.select("[data-lotto-id]") or soup.select("article.lotto") or
        soup.select(".risultato-asta") or soup.select(".lotto-card")
    )

    stubs = [s for s in (_stub_from_card(c) for c in cards) if s]

    # Totale risultati
    total = 0
    for sel in [".totale-risultati", "[class*='totale']", "h2", "h3"]:
        el = soup.select_one(sel)
        if el:
            nums = re.findall(r"\d+", el.get_text().replace(".", ""))
            if nums and int(nums[0]) > 10:
                total = int(nums[0])
                break

    log.info(f"  Cards: {len(stubs)} | Totale dichiarato: {total}")
    return stubs, total


def _stub_from_card(card) -> Optional[dict]:
    link = (
        card.select_one("a[href*='/annuncio']") or
        card.select_one("a[href*='/lotto']") or
        card.select_one("a[href]")
    )
    if not link:
        return None
    url    = urljoin(BASE_URL, link["href"])
    pvp_id = card.get("data-lotto-id") or card.get("data-id") or _id_from_url(url)

    data_pub = None
    for sel in ["time", "[class*='data']", "[class*='date']"]:
        el = card.select_one(sel)
        if el:
            data_pub = _parse_date(el.get("datetime") or el.get_text(strip=True))
            if data_pub:
                break

    return {"pvp_id": pvp_id, "url_dettaglio": url, "data_pubblicazione": data_pub}


# ── Parsing HTML dettaglio ────────────────────────────────────────────
def _parse_detail_html(html: str, url: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    d    = {"url_dettaglio": url, "pvp_id": _id_from_url(url)}

    def val(label: str) -> Optional[str]:
        for dt in soup.find_all("dt"):
            if re.search(label, dt.get_text(), re.I):
                dd = dt.find_next_sibling("dd")
                if dd: return dd.get_text(strip=True)
        for th in soup.find_all("th"):
            if re.search(label, th.get_text(), re.I):
                td = th.find_next_sibling("td")
                if td: return td.get_text(strip=True)
        for el in soup.find_all(text=re.compile(label, re.I)):
            sib = el.parent.find_next_sibling()
            if sib:
                t = sib.get_text(strip=True)
                if t and len(t) < 200: return t
        return None

    h1 = soup.select_one("h1, .titolo-asta, .lotto-title, .annuncio-title")
    d["titolo"]           = h1.get_text(strip=True) if h1 else None
    d["tribunale"]        = val(r"Tribunale")
    d["numero_procedura"] = val(r"Procedura|R\.G\.E\.|RGE|Numero proc")
    d["lotto"]            = val(r"\bLotto\b")
    d["tipo_asta"]        = val(r"Tipo.*(asta|procedura)|Esecuzione")
    d["tipologia"]        = val(r"Tipolog|Categor|Tipo.*bene|Tipo.*immobile")
    d["indirizzo"]        = val(r"Indirizzo|Via\b|Strada\b")
    d["comune"]           = val(r"\bComune\b|\bCittà\b")
    d["provincia"]        = val(r"\bProvincia\b")
    d["regione"]          = val(r"\bRegione\b")
    d["cap"]              = val(r"\bCAP\b|Codice postale")
    d["latitudine"]       = _extract_coord(soup, html, "lat")
    d["longitudine"]      = _extract_coord(soup, html, r"lng|lon")
    d["mq"]               = _num(val(r"Superficie|mq\b|m²"))
    d["vani"]             = _num(val(r"\bVani\b"))
    d["piano"]            = val(r"\bPiano\b")
    d["nr_locali"]        = _int(val(r"Locali|Stanze|Vani abitativi"))
    d["nr_bagni"]         = _int(val(r"Bagni|Servizi"))
    d["nr_posti_auto"]    = _int(val(r"Posto auto|Garage|Parcheggio"))
    d["foglio"]           = val(r"\bFoglio\b")
    d["particella"]       = val(r"Particella|Mappale")
    d["subalterno"]       = val(r"Subalterno|Sub\b")
    d["occupazione"]      = val(r"Occup|Disponibilit")
    d["prezzo_base"]      = _num(val(r"Prezzo base|Base d.asta|Valore stimato"))
    d["offerta_minima"]   = _num(val(r"Offerta minima"))
    d["rialzo_minimo"]    = _num(val(r"Rilancio|Rialzo minimo"))
    d["tipo_vendita"]     = val(r"Tipo.*vendita")
    d["modalita_vendita"] = val(r"Modalit|Sincrona|Asincrona|Telematica")
    d["data_vendita"]     = _parse_date(val(r"Data.*vendita|Data.*asta|Udienza"))
    d["data_pubblicazione"] = _parse_date(val(r"Pubblicaz|Pubblicato il"))
    d["data_scadenza"]    = _parse_date(val(r"Scadenza|Termine.*offert"))
    d["giudice"]          = val(r"\bGiudice\b")
    d["delegato"]         = val(r"Delegato|Professionista|Notaio")
    d["custode"]          = val(r"\bCustode\b")
    d["custode_tel"]      = val(r"Tel.*custode|Cellulare")
    d["custode_email"]    = _find_email(soup)
    desc = soup.select_one(".descrizione,.description,[class*='descr'],.testo-annuncio")
    d["descrizione"]      = desc.get_text(separator=" ", strip=True) if desc else None
    d["documenti"]        = _extract_docs(soup)
    return d


def _extract_docs(soup) -> list[dict]:
    docs, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href: continue
        if ".pdf" in href.lower() or any(
            k in href.lower() for k in ["allegat","document","perizia","avviso","planimetri","download"]
        ):
            url = urljoin(BASE_URL, href)
            if url in seen: continue
            seen.add(url)
            nome = a.get_text(strip=True) or href.split("/")[-1]
            tipo = _classify_doc(nome + href)
            docs.append({"nome": nome[:200], "url": url, "tipo": tipo})
    return docs


def _classify_doc(s: str) -> str:
    s = s.lower()
    if "perizia"    in s: return "perizia"
    if "avviso"     in s: return "avviso_vendita"
    if "planimetri" in s: return "planimetria"
    if "foto"       in s: return "fotografia"
    if "relazione"  in s: return "relazione"
    if "ordinanza"  in s: return "ordinanza"
    if "decreto"    in s: return "decreto"
    return "allegato"


def _extract_coord(soup, html: str, pattern: str) -> Optional[str]:
    for el in soup.find_all(True):
        for attr in ["data-lat","data-latitude","data-lng","data-lon","data-longitude"]:
            if re.search(pattern, attr, re.I) and el.get(attr):
                return el[attr]
    m = re.search(rf'["\']?{pattern}["\']?\s*[:=]\s*([+-]?\d{{1,3}}\.\d+)', html)
    return m.group(1) if m else None


def _find_email(soup) -> Optional[str]:
    m = re.search(r"[\w.+-]+@[\w-]+\.\w+", soup.get_text())
    return m.group() if m else None


def _id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/(\d{4,})", url)
    return m.group(1) if m else None


def _num(s: Optional[str]) -> Optional[float]:
    if not s: return None
    c = re.sub(r"[^\d,.]", "", s).replace(".", "").replace(",", ".")
    try: return float(c)
    except ValueError: return None


def _int(s: Optional[str]) -> Optional[int]:
    n = _num(s)
    return int(n) if n is not None else None


def _parse_date(s: Optional[str]) -> Optional[str]:
    if not s: return None
    for fmt in ["%d/%m/%Y","%d-%m-%Y","%Y-%m-%d","%d/%m/%Y %H:%M","%d/%m/%Y %H:%M:%S"]:
        try: return datetime.strptime(s.strip()[:19], fmt).isoformat()
        except ValueError: continue
    return s.strip()[:30]
