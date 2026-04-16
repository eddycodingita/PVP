"""
Scarica PDF in RAM, estrae il testo con pdfplumber, salva in DB.
Non salva il file su disco — tutto in memoria.

Uso:
    python scraper/pdf_downloader.py --limit 100
    python scraper/pdf_downloader.py --limit 500 --workers 5
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.env_check import load_dotenv
load_dotenv()

import asyncio, logging, argparse, io
from datetime import datetime, timezone
import httpx
import pdfplumber
from db.client import get_supabase

log = logging.getLogger("pdf_downloader")

MAX_PDF_MB   = 20      # salta PDF > 20MB
MAX_PAGES    = 10      # estrai solo prime 10 pagine
MAX_CHARS    = 50_000  # tronca testo a 50k caratteri
CONCURRENCY  = 5       # download paralleli
DELAY        = 0.3     # secondi tra download

# Tipi di documento prioritari per l'analisi AI
TIPI_PRIORITARI = {"perizia", "avviso_vendita", "ordinanza", "relazione", "planimetria"}


class PdfDownloader:
    def __init__(self, limit: int = 100, workers: int = CONCURRENCY, solo_prioritari: bool = False):
        self.limit           = limit
        self.workers         = workers
        self.solo_prioritari = solo_prioritari
        self.stats           = {"downloaded": 0, "errors": 0, "skipped": 0, "chars_total": 0}

    async def run(self) -> dict:
        sb = get_supabase()

        # Prendi documenti non ancora scaricati
        q = (
            sb.table("documenti")
            .select("id,asta_id,url_originale,nome_file,tipo")
            .eq("scaricato", False)
            .is_("errore", "null")
        )
        if self.solo_prioritari:
            q = q.in_("tipo", list(TIPI_PRIORITARI))

        res  = q.order("created_at", desc=True).limit(self.limit).execute()
        docs = res.data or []
        log.info(f"Documenti da scaricare: {len(docs)}")

        if not docs:
            log.info("Nessun documento da scaricare")
            return self.stats

        sem = asyncio.Semaphore(self.workers)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            await asyncio.gather(
                *[self._process(client, sem, doc) for doc in docs],
                return_exceptions=True,
            )

        log.info(
            f"Completato — downloaded={self.stats['downloaded']} "
            f"errors={self.stats['errors']} skipped={self.stats['skipped']}"
        )
        return self.stats

    async def _process(self, client: httpx.AsyncClient, sem: asyncio.Semaphore, doc: dict):
        async with sem:
            doc_id = doc["id"]
            url    = doc.get("url_originale")
            nome   = doc.get("nome_file") or "documento.pdf"

            if not url:
                self._mark_error(doc_id, "URL mancante")
                self.stats["skipped"] += 1
                return

            try:
                await asyncio.sleep(DELAY)

                # HEAD request per controllare dimensione
                try:
                    head = await client.head(url)
                    size_bytes = int(head.headers.get("content-length", 0))
                    size_mb    = size_bytes / (1024 * 1024)
                    if size_mb > MAX_PDF_MB:
                        log.warning(f"  Skip {nome}: {size_mb:.1f}MB > {MAX_PDF_MB}MB")
                        self._mark_error(doc_id, f"PDF troppo grande: {size_mb:.1f}MB")
                        self.stats["skipped"] += 1
                        return
                except Exception:
                    size_bytes = 0  # continua senza controllo dimensione

                # Scarica PDF in memoria
                resp = await client.get(url)
                if resp.status_code != 200:
                    self._mark_error(doc_id, f"HTTP {resp.status_code}")
                    self.stats["errors"] += 1
                    return

                pdf_bytes  = resp.content
                size_kb    = len(pdf_bytes) // 1024

                if len(pdf_bytes) > MAX_PDF_MB * 1024 * 1024:
                    self._mark_error(doc_id, f"PDF troppo grande: {size_kb}KB")
                    self.stats["skipped"] += 1
                    return

                # Estrai testo con pdfplumber (in RAM)
                testo, num_pagine = _extract_text(pdf_bytes)

                # Salva in DB
                get_supabase().table("documenti").update({
                    "testo_estratto": testo[:MAX_CHARS] if testo else None,
                    "num_pagine":     num_pagine,
                    "dimensione_kb":  size_kb,
                    "scaricato":      True,
                    "errore":         None,
                }).eq("id", doc_id).execute()

                self.stats["downloaded"]   += 1
                self.stats["chars_total"]  += len(testo or "")
                log.info(f"  ✓ {nome[:50]} | {size_kb}KB | {num_pagine}p | {len(testo or '')} chars")

            except Exception as e:
                log.error(f"  ✗ {nome[:50]}: {e}")
                self._mark_error(doc_id, str(e)[:200])
                self.stats["errors"] += 1

    def _mark_error(self, doc_id: str, msg: str):
        try:
            get_supabase().table("documenti").update({
                "errore": msg, "scaricato": False,
            }).eq("id", doc_id).execute()
        except Exception:
            pass


def _extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Estrae testo da PDF in memoria. Restituisce (testo, num_pagine)."""
    try:
        buf   = io.BytesIO(pdf_bytes)
        parts = []
        num_pagine = 0

        with pdfplumber.open(buf) as pdf:
            num_pagine = len(pdf.pages)
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                try:
                    testo_pagina = page.extract_text() or ""
                    if testo_pagina.strip():
                        parts.append(f"[Pagina {i+1}]\n{testo_pagina}")
                except Exception:
                    continue

        testo = "\n\n".join(parts)
        return testo, num_pagine

    except Exception as e:
        log.debug(f"  Errore estrazione: {e}")
        return "", 0


# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Scarica PDF e estrai testo")
    parser.add_argument("--limit",            type=int, default=100)
    parser.add_argument("--workers",          type=int, default=5)
    parser.add_argument("--solo-prioritari",  action="store_true",
                        help="Scarica solo perizie, avvisi, ordinanze")
    args = parser.parse_args()

    async def main():
        d = PdfDownloader(
            limit=args.limit,
            workers=args.workers,
            solo_prioritari=args.solo_prioritari,
        )
        s = await d.run()
        print(f"\nRisultato: {s}")

    asyncio.run(main())
