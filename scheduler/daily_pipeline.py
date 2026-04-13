"""
Pipeline giornaliera PVP Monitor.

ORDINE STEP (critico per correttezza degli alert):
  1-2. Scraping lista + dettaglio
  3.   Download PDF + OCR
  4.   Analisi AI + sconto perizia
  5.   Reconciler (aste rimosse)
  6.   Refresh materialized view   ← PRIMA degli alert
  7.   Alert                       ← usa v_aste_complete aggiornata
       (ma alerts.py interroga aste+analisi_ai direttamente — doppia sicurezza)

Uso:
    python scheduler/daily_pipeline.py              # solo annunci di oggi
    python scheduler/daily_pipeline.py --full       # tutti (primo avvio)
    python scheduler/daily_pipeline.py --analyze-only
    python scheduler/daily_pipeline.py --reconcile-only
    python scheduler/daily_pipeline.py --alert-only
"""
import asyncio
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.env_check import load_dotenv, check as check_env

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"run_{datetime.now().strftime('%Y%m%d_%H%M')}.log"),
    ],
)
log = logging.getLogger("pipeline")

SEP = "━" * 60


async def run_pipeline(mode: str = "daily"):
    # Valida env prima di tutto (#11)
    check_env(exit_on_error=True)

    from db.client import db_log_run_start, db_log_run_end, db_refresh_materialized_view

    run_id = db_log_run_start(mode)
    stats  = {
        "scraped": 0, "new": 0, "downloaded": 0,
        "analyzed": 0, "errors": 0,
        "costo_eur": 0.0, "tokens_in": 0, "tokens_out": 0,
    }

    try:
        # ── STEP 1-2: Scraping ─────────────────────────────────────────
        if mode not in ("analyze-only", "reconcile-only", "alert-only"):
            log.info(SEP)
            log.info("STEP 1-2: Scraping lista + dettaglio aste")
            from scraper.pvp_scraper import PVPScraper
            s = await PVPScraper(only_today=(mode == "daily")).run()
            stats.update({"scraped": s.get("scraped", 0), "new": s.get("new", 0)})
            stats["errors"] += s.get("errors", 0)
            log.info(f"Scraping: {s}")

        # ── STEP 3: Download PDF + OCR ─────────────────────────────────
        if mode not in ("analyze-only", "reconcile-only", "alert-only"):
            log.info(SEP)
            log.info("STEP 3: Download documenti + OCR")
            from scraper.document_downloader import DocumentDownloader
            s = await DocumentDownloader().run()
            stats["downloaded"]  = s.get("downloaded", 0)
            stats["errors"]     += s.get("errors", 0)
            log.info(f"Download: {s}")

        # ── STEP 4: Analisi AI ─────────────────────────────────────────
        if mode not in ("reconcile-only", "alert-only"):
            log.info(SEP)
            log.info("STEP 4: Analisi AI + sconto perizia + costi")
            from analysis.ai_analyzer import AstaAnalyzer
            s = await AstaAnalyzer().run_all_pending()
            stats["analyzed"]    = s.get("analyzed", 0)
            stats["errors"]     += s.get("errors", 0)
            stats["costo_eur"]  += s.get("costo_eur", 0.0)
            stats["tokens_in"]  += s.get("tokens_in", 0)
            stats["tokens_out"] += s.get("tokens_out", 0)
            log.info(f"Analisi: {s} | Costo: €{stats['costo_eur']:.4f}")

        # ── STEP 5: Reconciler ─────────────────────────────────────────
        if mode in ("daily", "full", "reconcile-only"):
            log.info(SEP)
            log.info("STEP 5: Reconciler — verifica aste rimosse")
            from scraper.reconciler import Reconciler
            s = await Reconciler().run()
            log.info(f"Reconciler: {s}")

        # ── STEP 6: Refresh materialized view ──────────────────────────
        # PRIMA degli alert: così v_aste_complete è aggiornata
        # (alerts.py usa comunque aste+analisi_ai direttamente come backup)
        log.info(SEP)
        log.info("STEP 6: Refresh materialized view")
        db_refresh_materialized_view()

        # ── STEP 7: Alert ──────────────────────────────────────────────
        if mode not in ("reconcile-only",):
            log.info(SEP)
            log.info("STEP 7: Alert — notifiche aste matching")
            from interface.alerts import AlertEngine
            s = await AlertEngine().run()
            log.info(f"Alert: sent={s.get('sent',0)} errors={s.get('errors',0)}")

        # ── Fine ───────────────────────────────────────────────────────
        db_log_run_end(run_id, "completed", stats)
        log.info(SEP)
        log.info("PIPELINE COMPLETATA ✓")
        for k, v in stats.items():
            if v:
                label = k.replace("_", " ").capitalize()
                log.info(f"  {label:<22} {v}")

    except Exception as e:
        log.error(f"Pipeline fallita: {e}", exc_info=True)
        stats["errors"] += 1
        db_log_run_end(run_id, "failed", stats, str(e))
        raise

    return stats


if __name__ == "__main__":
    args = sys.argv[1:]
    if   "--full"            in args: mode = "full"
    elif "--analyze-only"    in args: mode = "analyze-only"
    elif "--reconcile-only"  in args: mode = "reconcile-only"
    elif "--alert-only"      in args: mode = "alert-only"
    else:                             mode = "daily"

    asyncio.run(run_pipeline(mode))
