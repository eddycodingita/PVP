"""
STEP 3: Download PDF + estrazione testo.

#1 — Retry con backoff esponenziale su ogni download (via utils/http.py).
#2 — Verifica magic bytes PDF: se il server risponde con HTML di errore
     invece del PDF, il documento viene marcato come errore.
#3 — OCR fallback per PDF scansionati (pytesseract).

Sistema: apt-get install tesseract-ocr tesseract-ocr-ita poppler-utils
"""
import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

import pdfplumber

from db.client import db_get_documenti_da_scaricare, db_update_documento, get_supabase
from utils.pvp_http import http_get_with_retry

log = logging.getLogger("downloader")

CONCURRENCY       = 4
MAX_PDF_MB        = 50
MIN_CHARS_FOR_OCR = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Accept":     "application/pdf,*/*",
    "Referer":    "https://pvp.giustizia.it/",
}


class DocumentDownloader:
    def __init__(self):
        self.sb    = get_supabase()
        self.stats = {"downloaded": 0, "ocr_used": 0, "errors": 0, "skipped": 0}

    async def run(self) -> dict:
        docs = db_get_documenti_da_scaricare()
        log.info(f"Documenti da scaricare: {len(docs)}")
        sem = asyncio.Semaphore(CONCURRENCY)
        await asyncio.gather(*[self._process(sem, d) for d in docs])
        return self.stats

    async def _process(self, sem: asyncio.Semaphore, doc: dict):
        async with sem:
            doc_id = doc["id"]
            url    = doc["url_originale"]
            nome   = doc.get("nome_file") or "allegato"
            try:
                log.info(f"  ↓ {nome[:60]}")

                # #1 — Download con retry automatico
                resp = await http_get_with_retry(url, headers=HEADERS, timeout=60)
                if resp is None:
                    return self._err(doc_id, "download fallito dopo tutti i retry")

                # #2 — Verifica magic bytes: deve essere un PDF reale
                ct      = resp.headers.get("content-type", "")
                content = resp.content
                if not _is_real_pdf(content, ct, url):
                    log.warning(f"    Non è un PDF valido: {ct} ({len(content)}B)")
                    return self._err(doc_id, f"risposta non-PDF: {ct[:80]}")

                size_kb = len(content) // 1024
                if size_kb > MAX_PDF_MB * 1024:
                    self.stats["skipped"] += 1
                    return self._err(doc_id, f"troppo grande: {size_kb}KB")

                # #3 — Estrazione testo + OCR fallback
                testo, pagine, ocr_usato = _extract_with_ocr_fallback(content)
                if ocr_usato:
                    self.stats["ocr_used"] += 1

                # Upload su Supabase Storage
                storage_path = f"{doc['asta_id']}/{doc_id}_{_safe_name(nome)}"
                try:
                    self.sb.storage.from_("documenti-aste").upload(
                        path=storage_path, file=content,
                        file_options={"content-type": "application/pdf", "upsert": "true"},
                    )
                except Exception as e:
                    log.warning(f"    Storage: {e}")
                    storage_path = None

                db_update_documento(doc_id, {
                    "storage_path":   storage_path,
                    "testo_estratto": testo,
                    "num_pagine":     pagine,
                    "dimensione_kb":  size_kb,
                    "scaricato":      True,
                    "errore":         None,
                })
                self.stats["downloaded"] += 1
                log.info(f"    ✓ {size_kb}KB · {pagine}pp · {len(testo)}chars{' · OCR' if ocr_usato else ''}")

            except Exception as e:
                log.error(f"  ✗ {url}: {e}")
                self._err(doc_id, str(e)[:200])

    def _err(self, doc_id: str, msg: str):
        db_update_documento(doc_id, {"errore": msg, "scaricato": False})
        self.stats["errors"] += 1


# ── #2: Validazione risposta ──────────────────────────────────────────
def _is_real_pdf(content: bytes, content_type: str, url: str) -> bool:
    if len(content) < 200:
        return False
    # Magic bytes %PDF
    if content[:4] == b"%PDF":
        return True
    # HTML di errore
    head = content[:300].lower()
    if b"<html" in head or b"<!doc" in head or b"<body" in head:
        return False
    # Fallback: url o content-type
    return "pdf" in content_type.lower() or url.lower().endswith(".pdf")


# ── #3: Estrazione testo con OCR fallback ─────────────────────────────
def _extract_with_ocr_fallback(pdf_bytes: bytes) -> tuple[str, int, bool]:
    testo, pagine = _pdfplumber(pdf_bytes)
    if len(testo.strip()) >= MIN_CHARS_FOR_OCR:
        return testo, pagine, False
    testo_ocr, pagine_ocr = _ocr(pdf_bytes)
    if len(testo_ocr) > len(testo):
        return testo_ocr, pagine_ocr, True
    return testo, pagine, False


def _pdfplumber(pdf_bytes: bytes) -> tuple[str, int]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes); tmp = f.name
    try:
        pages = []
        with pdfplumber.open(tmp) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for table in (page.extract_tables() or []):
                    for row in table:
                        text += "\n" + " | ".join(str(c or "") for c in row)
                pages.append(text)
        return re.sub(r"\n{3,}", "\n\n", "\n\n--- PAGINA ---\n\n".join(pages)).strip(), len(pages)
    except Exception as e:
        log.warning(f"pdfplumber: {e}"); return "", 0
    finally:
        Path(tmp).unlink(missing_ok=True)


def _ocr(pdf_bytes: bytes) -> tuple[str, int]:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(pdf_bytes, dpi=200, fmt="jpeg")
        pages  = [pytesseract.image_to_string(img.convert("L"), lang="ita", config="--psm 3")
                  for img in images]
        return re.sub(r"\n{3,}", "\n\n", "\n\n--- PAGINA ---\n\n".join(pages)).strip(), len(pages)
    except ImportError:
        log.warning("pytesseract/pdf2image non installati"); return "", 0
    except Exception as e:
        log.warning(f"OCR: {e}"); return "", 0


def _safe_name(nome: str) -> str:
    nome = re.sub(r"[^\w\-.]", "_", nome)[:100]
    return nome if nome.endswith(".pdf") else nome + ".pdf"
