"""
Backfill iniziale — importa tutte le aste dal PVP in Supabase.
Strategia: naviga le pagine HTML e intercetta le risposte API di rete.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, json, logging
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from db.client import get_supabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("backfill.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("backfill")

BASE      = "https://pvp.giustizia.it/pvp/it/lista_annunci.page"
PAGE_SIZE = 20
BATCH_DB  = 200

RECORD_KEYS = [
    "pvp_id", "url_dettaglio", "data_pubblicazione", "tribunale",
    "numero_procedura", "lotto", "tipologia", "indirizzo", "comune",
    "provincia", "latitudine", "longitudine", "prezzo_base",
    "offerta_minima", "rialzo_minimo", "data_vendita", "descrizione",
    "occupazione", "is_active", "scraped_at",
]


def _parse_disp(disp: list) -> str:
    if not disp:
        return None
    vals = [str(d).upper() for d in disp]
    if all("LIBER" in v for v in vals): return "Libero"
    if any("OCCUP" in v for v in vals): return "Occupato"
    return "Parzialmente libero"


def _to_record(item: dict) -> dict:
    ind  = item.get("indirizzo") or {}
    cord = ind.get("coordinate") or {}
    record = {k: None for k in RECORD_KEYS}
    record.update({
        "pvp_id":             str(item["id"]),
        "url_dettaglio":      f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={item['id']}",
        "data_pubblicazione": item.get("dataPubblicazione"),
        "tribunale":          item.get("tribunale"),
        "numero_procedura":   item.get("procedura"),
        "lotto":              item.get("numeroLotto"),
        "tipologia":          item.get("categoriaLotto"),
        "indirizzo":          ind.get("via"),
        "comune":             ind.get("citta"),
        "provincia":          ind.get("provincia"),
        "latitudine":         cord.get("latitudine"),
        "longitudine":        cord.get("longitudine"),
        "prezzo_base":        item.get("prezzoBaseAsta"),
        "offerta_minima":     item.get("offertaMinima"),
        "rialzo_minimo":      item.get("rialzoMinimo"),
        "data_vendita":       item.get("dataVendita"),
        "descrizione":        item.get("descLotto"),
        "occupazione":        _parse_disp(item.get("disponibilita") or []),
        "is_active":          True,
        "scraped_at":         datetime.now(timezone.utc).isoformat(),
    })
    return record


def _flush(sb, batch_dict: dict) -> int:
    records = list(batch_dict.values())
    if not records:
        return 0
    sb.table("aste").upsert(records, on_conflict="pvp_id").execute()
    return len(records)


async def run():
    sb = get_supabase()

    # Conta quante pagine ci sono dalla prima navigazione
    total_pages = None
    total_elem  = None

    async with async_playwright() as p:
        browser  = await p.chromium.launch(headless=True)
        ctx      = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            locale="it-IT",
        )
        page_obj = await ctx.new_page()

        batch_dict  = {}
        salvate     = 0
        errori      = 0
        page_count  = 0

        # Evento per catturare la risposta API
        api_event = asyncio.Event()
        current_items = []

        async def on_response(response):
            nonlocal total_pages, total_elem
            if "ricerca/vendite" in response.url:
                try:
                    data    = await response.json()
                    body    = data.get("body", {})
                    content = body.get("content", [])
                    if total_pages is None:
                        total_pages = body.get("totalPages", 0)
                        total_elem  = body.get("totalElements", 0)
                        log.info(f"Aste API: {total_elem} | Pagine: {total_pages}")
                    current_items.clear()
                    current_items.extend(content)
                    api_event.set()
                except:
                    api_event.set()  # sblocca anche in caso di errore

        page_obj.on("response", on_response)

        # Prima pagina per ottenere il totale
        url = f"{BASE}?searchType=searchForm&page=0&size={PAGE_SIZE}&codTipoLotto=IMMOBILI&nazione=Italia"
        await page_obj.goto(url)
        await asyncio.wait_for(api_event.wait(), timeout=15)
        api_event.clear()

        if total_pages is None:
            log.error("Impossibile ottenere il totale pagine")
            await browser.close()
            return

        # Processa la prima pagina
        for item in current_items:
            batch_dict[str(item["id"])] = _to_record(item)
        page_count = 1

        # Scorri tutte le pagine rimanenti
        for page_num in range(1, total_pages):
            try:
                api_event.clear()
                current_items.clear()

                url = f"{BASE}?searchType=searchForm&page={page_num}&size={PAGE_SIZE}&codTipoLotto=IMMOBILI&nazione=Italia"
                await page_obj.goto(url)
                await asyncio.wait_for(api_event.wait(), timeout=15)

                for item in current_items:
                    batch_dict[str(item["id"])] = _to_record(item)

                page_count += 1

                # Flush batch
                if len(batch_dict) >= BATCH_DB:
                    salvate += _flush(sb, batch_dict)
                    batch_dict = {}

            except asyncio.TimeoutError:
                log.warning(f"  Timeout pagina {page_num} — riprovo")
                errori += 1
                await asyncio.sleep(3)
                continue
            except Exception as e:
                log.error(f"  Errore pagina {page_num}: {e}")
                errori += 1
                continue

            if page_count % 500 == 0:
                log.info(
                    f"  Pagina {page_count}/{total_pages} | "
                    f"Salvate: {salvate} | Errori: {errori}"
                )

        # Flush finale
        if batch_dict:
            salvate += _flush(sb, batch_dict)

        await browser.close()

    log.info(f"BACKFILL COMPLETATO - Salvate: {salvate} | Errori: {errori}")


asyncio.run(run())
