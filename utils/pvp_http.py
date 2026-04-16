"""
utils/http.py — Retry con backoff esponenziale (#1) e rilevamento blocco IP (#2).

Usato da pvp_scraper.py, document_downloader.py e reconciler.py.
"""
import asyncio
import logging
import random
import re
from typing import Optional

import httpx
from playwright.async_api import Page, BrowserContext

log = logging.getLogger("http")

# ── Configurazione retry ───────────────────────────────────────────────
RETRY_MAX       = 4        # tentativi massimi
RETRY_BASE_WAIT = 3.0      # secondi base per backoff
RETRY_MAX_WAIT  = 90.0     # cap massimo attesa

# ── Segnali di blocco IP / captcha ────────────────────────────────────
BLOCK_KEYWORDS = [
    "captcha", "robot", "blocked", "troppo", "too many",
    "rate limit", "accesso negato", "access denied",
    "errore temporaneo", "servizio non disponibile",
    "cloudflare", "ddos", "challenge",
]

BLOCK_HTTP_CODES = {429, 503, 403}


# ── Retry decorator per chiamate httpx ────────────────────────────────
async def http_get_with_retry(
    url: str,
    headers: dict,
    timeout: float = 30,
    retries: int = RETRY_MAX,
) -> Optional[httpx.Response]:
    """
    GET con retry esponenziale. Gestisce timeout, 5xx, 429.
    Restituisce None se tutti i tentativi falliscono.
    """
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(url)

            # Blocco esplicito HTTP
            if resp.status_code in BLOCK_HTTP_CODES:
                wait = _backoff(attempt)
                log.warning(f"  HTTP {resp.status_code} — attendo {wait:.0f}s (tentativo {attempt}/{retries})")
                if resp.status_code == 429:
                    # Rispetta Retry-After se presente
                    retry_after = int(resp.headers.get("retry-after", wait))
                    wait = max(wait, retry_after)
                await asyncio.sleep(wait)
                continue

            # Errori server transitori
            if resp.status_code >= 500:
                wait = _backoff(attempt)
                log.warning(f"  HTTP {resp.status_code} — retry {attempt}/{retries} tra {wait:.0f}s")
                await asyncio.sleep(wait)
                continue

            return resp

        except httpx.TimeoutException:
            wait = _backoff(attempt)
            log.warning(f"  Timeout — retry {attempt}/{retries} tra {wait:.0f}s: {url[:80]}")
            await asyncio.sleep(wait)

        except httpx.ConnectError as e:
            wait = _backoff(attempt)
            log.warning(f"  ConnectError ({e}) — retry {attempt}/{retries} tra {wait:.0f}s")
            await asyncio.sleep(wait)

        except Exception as e:
            log.error(f"  Errore inatteso: {e}")
            return None

    log.error(f"  Tutti i retry esauriti per: {url[:80]}")
    return None


# ── Playwright: carica pagina con retry (#1) e blocco detection (#2) ──
async def playwright_get_with_retry(
    ctx: BrowserContext,
    url: str,
    wait_selector: Optional[str] = None,
    retries: int = RETRY_MAX,
) -> Optional[str]:
    """
    Carica una pagina con Playwright, riprova in caso di timeout o blocco.
    Restituisce l'HTML renderizzato, o None se tutti i retry falliscono.
    """
    for attempt in range(1, retries + 1):
        page: Optional[Page] = None
        try:
            page = await ctx.new_page()

            # Intercetta le risposte per rilevare blocchi
            blocked = False
            async def check_response(resp):
                nonlocal blocked
                if resp.status in BLOCK_HTTP_CODES and "pvp.giustizia.it" in resp.url:
                    blocked = True
            page.on("response", check_response)

            await page.goto(url, wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(1.0)

            html = await page.content()

            # #2 — Rilevamento blocco da contenuto HTML
            block_type = _detect_block(html)
            if block_type or blocked:
                wait = _backoff(attempt)
                log.warning(
                    f"  🚫 Blocco rilevato ({block_type or 'HTTP'}) — "
                    f"attendo {wait:.0f}s (tentativo {attempt}/{retries})"
                )
                await asyncio.sleep(wait)
                continue

            # Attesa selettore opzionale (conferma rendering)
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=8_000)
                    html = await page.content()
                except Exception:
                    pass  # Il selettore non è critico

            return html

        except Exception as e:
            err_str = str(e).lower()
            is_timeout = "timeout" in err_str or "30000ms" in err_str

            if is_timeout:
                wait = _backoff(attempt)
                log.warning(f"  Timeout Playwright — retry {attempt}/{retries} tra {wait:.0f}s")
                await asyncio.sleep(wait)
            else:
                log.error(f"  Errore Playwright: {e}")
                if attempt >= retries:
                    return None
                await asyncio.sleep(_backoff(attempt))

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    log.error(f"  Tutti i retry Playwright esauriti per: {url[:80]}")
    return None


# ── Helpers ───────────────────────────────────────────────────────────
def _backoff(attempt: int) -> float:
    """Backoff esponenziale con jitter: base * 2^(attempt-1) ± 20%."""
    raw  = RETRY_BASE_WAIT * (2 ** (attempt - 1))
    raw  = min(raw, RETRY_MAX_WAIT)
    jitter = raw * 0.2 * (random.random() * 2 - 1)   # ±20%
    return max(1.0, raw + jitter)


def _detect_block(html: str) -> Optional[str]:
    """Cerca nel testo della pagina segnali di blocco. Restituisce il keyword trovato o None."""
    lower = html.lower()
    # Pagina troppo corta = probabile errore / redirect
    if len(html.strip()) < 500:
        return "pagina_vuota"
    for kw in BLOCK_KEYWORDS:
        if kw in lower:
            return kw
    return None


def is_block_response(html: str) -> bool:
    return _detect_block(html) is not None
