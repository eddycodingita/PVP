"""
STEP 5: Reconciler — marca come inattive le aste rimosse dal PVP.

#1 — Retry con backoff su ogni HEAD request.
#4 — Logica: HEAD request rapida su ogni asta attiva in DB.
     404 o redirect a pagina di errore → is_active = FALSE.
"""
import asyncio
import logging
from typing import Optional

from db.client import db_get_all_active_pvp_ids, db_mark_inactive
from utils.http import http_get_with_retry

log = logging.getLogger("reconciler")

BASE_URL    = "https://pvp.giustizia.it/pvp/it/annuncio.page?id="
CONCURRENCY = 8
TIMEOUT     = 12

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0",
    "Referer":    "https://pvp.giustizia.it/",
}

# Pattern URL di "not found" del PVP
NOT_FOUND_PATTERNS = ["errore", "not-found", "404", "homepage", "not_found", "pagenotfound"]


class Reconciler:
    def __init__(self):
        self.stats = {"checked": 0, "removed": 0, "errors": 0}

    async def run(self) -> dict:
        pvp_ids = db_get_all_active_pvp_ids()
        log.info(f"Reconciler: verifico {len(pvp_ids)} aste attive")
        if not pvp_ids:
            return self.stats

        sem     = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(
            *[self._check(sem, pid) for pid in pvp_ids],
            return_exceptions=True,
        )

        to_deactivate = [
            pvp_ids[i] for i, r in enumerate(results) if r == "REMOVED"
        ]

        if to_deactivate:
            log.info(f"  Aste da disattivare: {len(to_deactivate)}")
            for i in range(0, len(to_deactivate), 50):
                db_mark_inactive(to_deactivate[i:i+50])
            self.stats["removed"] = len(to_deactivate)

        log.info(f"  Reconciler: checked={self.stats['checked']} removed={self.stats['removed']} errors={self.stats['errors']}")
        return self.stats

    async def _check(self, sem: asyncio.Semaphore, pvp_id: str) -> str:
        async with sem:
            url = BASE_URL + pvp_id
            try:
                # #1 — Usa il retry centralizzato (max 2 tentativi: veloce, non critico)
                resp = await http_get_with_retry(
                    url, headers=HEADERS, timeout=TIMEOUT, retries=2
                )
                self.stats["checked"] += 1

                if resp is None:
                    # Tutti i retry falliti — non è un 404 confermato, ignora
                    self.stats["errors"] += 1
                    return "ERROR"

                if resp.status_code == 404:
                    log.info(f"  🗑  Rimossa (404): {pvp_id}")
                    return "REMOVED"

                # Redirect verso pagina di errore
                final_url = str(resp.url).lower()
                if any(p in final_url for p in NOT_FOUND_PATTERNS):
                    log.info(f"  🗑  Rimossa (redirect→errore): {pvp_id}")
                    return "REMOVED"

                # Risposta HTML molto corta = probabile pagina di errore
                ct = resp.headers.get("content-type", "")
                if "html" in ct and len(resp.content) < 1000:
                    # Controlla contenuto
                    body = resp.text.lower()
                    if any(p in body for p in ["non trovato", "not found", "pagina non esiste", "annuncio non"]):
                        log.info(f"  🗑  Rimossa (body errore): {pvp_id}")
                        return "REMOVED"

                return "OK"

            except Exception as e:
                log.warning(f"  Reconciler {pvp_id}: {e}")
                self.stats["errors"] += 1
                return "ERROR"
