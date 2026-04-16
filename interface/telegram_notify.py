"""
Invia notifica Telegram con il riepilogo delle aste scrappate oggi.

Uso:
    python interface/telegram_notify.py
    python interface/telegram_notify.py --test   # messaggio di test
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.env_check import load_dotenv
load_dotenv()

import httpx, argparse, logging
from datetime import date, datetime, timezone

log = logging.getLogger("telegram")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
API_URL   = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_message(text: str) -> bool:
    """Invia messaggio Telegram. Restituisce True se successo."""
    if not BOT_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati")
        return False
    try:
        r = httpx.post(API_URL, json={
            "chat_id":    CHAT_ID,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=10)
        r.raise_for_status()
        log.info(f"Notifica inviata: {text[:50]}...")
        return True
    except Exception as e:
        log.error(f"Errore invio Telegram: {e}")
        return False


def get_daily_stats() -> dict:
    """Recupera statistiche di oggi da Supabase."""
    from db.client import get_supabase
    sb    = get_supabase()
    oggi  = date.today().isoformat()

    # Nuove aste pubblicate oggi
    res_nuove = sb.table("aste").select("id").gte(
        "data_pubblicazione", oggi).eq("is_active", True).limit(1000).execute()
    nuove = len(res_nuove.data or [])

    # Aste con sconto perizia > 30%
    res_sconti = sb.table("analisi_ai").select("asta_id").gte(
        "sconto_perizia_pct", 30).limit(1000).execute()
    con_sconto = len(res_sconti.data or [])

    # Aste con rischio basso (1-3) pubblicate oggi
    res_buone = (sb.table("v_aste_complete")
        .select("pvp_id,comune,prezzo_base,sconto_perizia_pct,punteggio_rischio")
        .gte("data_pubblicazione", oggi)
        .lte("punteggio_rischio", 3)
        .not_.is_("punteggio_rischio", "null")
        .order("sconto_perizia_pct", desc=True)
        .limit(5)
        .execute())
    buone = res_buone.data or []

    # Totale aste analizzate oggi
    res_analizzate = (sb.table("analisi_ai")
        .select("asta_id")
        .gte("created_at", oggi)
        .limit(1000)
        .execute())
    analizzate = len(res_analizzate.data or [])

    return {
        "oggi":       oggi,
        "nuove":      nuove,
        "analizzate": analizzate,
        "con_sconto": con_sconto,
        "buone":      buone,
    }


def build_message(stats: dict) -> str:
    oggi     = stats["oggi"]
    nuove    = stats["nuove"]
    analizzate = stats["analizzate"]
    buone    = stats["buone"]

    msg = f"🏠 <b>PVP Monitor — {oggi}</b>\n\n"

    if nuove == 0:
        msg += "📭 Nessuna nuova asta pubblicata oggi.\n"
    else:
        msg += f"📬 <b>{nuove} nuove aste</b> pubblicate oggi\n"
        msg += f"🤖 {analizzate} analizzate con AI\n"

    if buone:
        msg += f"\n⭐ <b>Migliori opportunità oggi</b> (rischio ≤ 3):\n"
        for a in buone:
            prezzo = f"€{int(a['prezzo_base']):,}".replace(",", ".") if a.get("prezzo_base") else "N/D"
            sconto = f" 📉{a['sconto_perizia_pct']}%" if a.get("sconto_perizia_pct") else ""
            comune = a.get("comune") or "N/D"
            pvp_id = a.get("pvp_id")
            url    = f"https://pvp.giustizia.it/pvp/it/detail_annuncio.page?idAnnuncio={pvp_id}"
            msg   += f"• <a href='{url}'>{comune}</a> — {prezzo}{sconto}\n"

    msg += f"\n🔗 <a href='https://pvp-monitor2.vercel.app'>Apri PVP Monitor</a>"
    return msg


def send_test():
    """Invia messaggio di test."""
    msg = (
        "🏠 <b>PVP Monitor — Test</b>\n\n"
        "✅ Bot configurato correttamente!\n"
        "Riceverai questa notifica ogni mattina dopo lo scraping.\n\n"
        f"🔗 <a href='https://pvp-monitor2.vercel.app'>Apri PVP Monitor</a>"
    )
    ok = send_message(msg)
    print("✓ Messaggio di test inviato!" if ok else "✗ Errore invio")


def send_daily():
    """Invia riepilogo giornaliero."""
    stats = get_daily_stats()
    msg   = build_message(stats)
    ok    = send_message(msg)
    print(f"{'✓' if ok else '✗'} Notifica giornaliera {'inviata' if ok else 'FALLITA'}")
    print(f"  Nuove: {stats['nuove']} | Analizzate: {stats['analizzate']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Invia messaggio di test")
    args = parser.parse_args()

    if args.test:
        send_test()
    else:
        send_daily()
