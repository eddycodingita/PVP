"""
#7 — Sistema di alert: notifica via email o Telegram quando escono
aste che soddisfano le regole configurate.

FIX: interroga direttamente la tabella `aste` + JOIN `analisi_ai`
invece della materialized view (che viene refreshata DOPO gli alert).

Canali:
  - email   → Resend API (resend.com, 3000 email/mese gratis)
  - telegram → Bot API di Telegram

Configurazione alert: usa manage_alerts.py oppure inserisci direttamente in DB.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from db.client import (
    db_get_active_alert_rules, db_alert_gia_inviato,
    db_log_alert, db_update_alert_timestamp, get_supabase,
)

log = logging.getLogger("alerts")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = os.environ.get("ALERT_FROM_EMAIL", "pvp-monitor@tuodominio.com")


class AlertEngine:
    def __init__(self):
        self.stats = {"checked": 0, "sent": 0, "errors": 0}

    async def run(self) -> dict:
        rules = db_get_active_alert_rules()
        if not rules:
            log.info("Nessuna alert rule attiva")
            return self.stats

        log.info(f"Alert rules attive: {len(rules)}")

        # FIX: interroga aste+analisi direttamente, non la materialized view
        # La MV viene refreshata DOPO gli alert — userebbe dati vecchi
        da = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        aste_nuove = _fetch_aste_nuove(da)
        log.info(f"Aste nuove da verificare: {len(aste_nuove)}")

        for rule in rules:
            await self._process_rule(rule, aste_nuove)

        return self.stats

    async def _process_rule(self, rule: dict, aste: list[dict]):
        rule_id = rule["id"]
        matches = [a for a in aste if _matches(a, rule)]
        log.info(f"  Regola '{rule['nome']}': {len(matches)} match")

        for asta in matches:
            self.stats["checked"] += 1
            asta_id = asta["id"]
            pvp_id  = asta["pvp_id"]

            if db_alert_gia_inviato(rule_id, asta_id):
                continue

            ok, err = await self._send(rule, asta)
            db_log_alert(rule_id, asta_id, pvp_id, rule["canale"], ok, err)

            if ok:
                self.stats["sent"] += 1
                log.info(f"    ✉ Inviato: {pvp_id} → {rule['destinatario'][:30]}")
            else:
                self.stats["errors"] += 1
                log.warning(f"    ✗ Invio fallito: {err}")

        db_update_alert_timestamp(rule_id)

    async def _send(self, rule: dict, asta: dict) -> tuple[bool, Optional[str]]:
        if rule.get("canale") == "telegram":
            return await _send_telegram(rule["destinatario"], asta)
        return await _send_email(rule["destinatario"], rule["nome"], asta)


# ── Query aste nuove (senza materialized view) ────────────────────────
def _fetch_aste_nuove(da: str) -> list[dict]:
    """
    Prende aste scrappate di recente con il loro eventuale record analisi_ai.
    JOIN esplicito invece di v_aste_complete per avere dati freschi.
    """
    sb = get_supabase()
    res = (
        sb.table("aste")
        .select(
            "id,pvp_id,url_dettaglio,tribunale,comune,provincia,regione,"
            "tipologia,prezzo_base,mq,occupazione,data_pubblicazione,"
            "analisi_ai(punteggio_rischio,problemi_rilevati,"
            "descrizione_sintetica,valore_perizia,sconto_perizia_pct,"
            "occupato_terzi,abuso_edilizio,ipoteca_presente,"
            "necessita_ristrutturazione,amianto_presente)"
        )
        .eq("is_active", True)
        .gte("scraped_at", da)
        .execute()
    )
    # Appiattisci il join: porta i campi di analisi_ai al livello superiore
    rows = []
    for r in (res.data or []):
        flat = {k: v for k, v in r.items() if k != "analisi_ai"}
        ai = r.get("analisi_ai") or {}
        if isinstance(ai, list):
            ai = ai[0] if ai else {}
        flat.update(ai)
        rows.append(flat)
    return rows


# ── Matching ──────────────────────────────────────────────────────────
def _matches(asta: dict, rule: dict) -> bool:
    def ilike(field, key):
        rv, av = rule.get(key), asta.get(field)
        if rv is None: return True
        if av is None: return False
        return str(rv).lower() in str(av).lower()

    def lte(field, key):
        rv, av = rule.get(key), asta.get(field)
        if rv is None: return True
        if av is None: return False
        try: return float(av) <= float(rv)
        except: return False

    def gte(field, key):
        rv, av = rule.get(key), asta.get(field)
        if rv is None: return True
        if av is None: return False
        try: return float(av) >= float(rv)
        except: return False

    if not ilike("provincia", "provincia"): return False
    if not ilike("regione",   "regione"):   return False
    if not ilike("comune",    "comune"):    return False
    if not ilike("tipologia", "tipologia"): return False
    if not lte("prezzo_base", "prezzo_max"): return False
    if not gte("prezzo_base", "prezzo_min"): return False
    if not gte("mq",          "mq_min"):    return False

    if rule.get("solo_libere"):
        if not str(asta.get("occupazione", "")).lower().startswith("libero"):
            return False

    if rule.get("rischio_max") is not None:
        r = asta.get("punteggio_rischio")
        if r and int(r) > int(rule["rischio_max"]):
            return False

    # Problemi che l'asta NON deve avere
    for p in (rule.get("problemi_esclusi") or []):
        if asta.get(p) is True:
            return False
        if p in (asta.get("problemi_rilevati") or []):
            return False

    return True


# ── Invio Telegram ────────────────────────────────────────────────────
async def _send_telegram(chat_id: str, asta: dict) -> tuple[bool, Optional[str]]:
    if not TELEGRAM_TOKEN:
        return False, "TELEGRAM_BOT_TOKEN non configurato"

    rischio = asta.get("punteggio_rischio")
    sconto  = asta.get("sconto_perizia_pct")
    prezzo  = asta.get("prezzo_base") or 0
    problemi = asta.get("problemi_rilevati") or []

    lines = [
        f"🏠 *Nuova asta PVP #{asta.get('pvp_id')}*",
        f"📍 {asta.get('comune')} ({asta.get('provincia')}) · {asta.get('tipologia') or ''}",
        f"💶 Prezzo: *€{float(prezzo):,.0f}*",
    ]
    if sconto:
        lines.append(f"📉 Sconto perizia: *{sconto}%*")
    if asta.get("mq"):
        lines.append(f"📐 {asta['mq']} mq")
    if asta.get("occupazione"):
        emoji = "✅" if "libero" in str(asta["occupazione"]).lower() else "⛔"
        lines.append(f"{emoji} {asta['occupazione']}")
    if rischio:
        e = "🟢" if rischio <= 3 else "🟡" if rischio <= 6 else "🔴"
        lines.append(f"{e} Rischio: {rischio}/10")
    if problemi:
        lines.append(f"⚠️ {', '.join(problemi[:5])}")
    if asta.get("descrizione_sintetica"):
        lines.append(f"\n_{asta['descrizione_sintetica'][:250]}_")
    if asta.get("url_dettaglio"):
        lines.append(f"\n🔗 [Vedi sul PVP]({asta['url_dettaglio']})")

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": "\n".join(lines),
                      "parse_mode": "Markdown", "disable_web_page_preview": False},
            )
            r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)


# ── Invio Email (Resend) ──────────────────────────────────────────────
async def _send_email(to: str, rule_nome: str, asta: dict) -> tuple[bool, Optional[str]]:
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY non configurato"

    prezzo   = float(asta.get("prezzo_base") or 0)
    sconto   = asta.get("sconto_perizia_pct")
    problemi = asta.get("problemi_rilevati") or []
    rischio  = asta.get("punteggio_rischio")

    subject = f"🏠 Nuova asta: {asta.get('comune')} — €{prezzo:,.0f}"
    if sconto:
        subject += f" (sconto {sconto}%)"

    tags_html = "".join(
        f'<span style="background:#fef3cd;border:1px solid #ffc107;'
        f'border-radius:3px;padding:2px 7px;font-size:12px;margin:2px;display:inline-block">{p}</span>'
        for p in problemi[:6]
    )

    body = f"""
<div style="font-family:sans-serif;max-width:580px;margin:0 auto;color:#333">
  <div style="background:#0d1117;color:#c9a84c;padding:20px;border-radius:8px 8px 0 0">
    <h2 style="margin:0;font-size:17px">🏠 {rule_nome}</h2>
    <p style="margin:4px 0 0;font-size:12px;opacity:.7">PVP Monitor · Nuova asta trovata</p>
  </div>
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:0 0 8px 8px;padding:24px">
    <p style="font-size:13px;color:#888;margin:0 0 4px">
      PVP#{asta.get('pvp_id')} · {asta.get('tribunale') or ''}
    </p>
    <h3 style="margin:0 0 4px">{asta.get('comune')} ({asta.get('provincia')}) · {asta.get('tipologia') or ''}</h3>
    <p style="font-size:26px;font-weight:bold;color:#0d1117;margin:8px 0">€{prezzo:,.0f}</p>
    {f'<p style="color:#2e7d32;font-weight:500;margin:0 0 12px">📉 Sconto su perizia: {sconto}%</p>' if sconto else ''}
    <table style="border-collapse:collapse;width:100%;margin:12px 0">
      {''.join(f'<tr><td style="padding:5px 8px;color:#888;font-size:13px">{l}</td>'
               f'<td style="padding:5px 8px;font-size:13px">{v}</td></tr>'
               for l, v in [
                 ('Superficie', f"{asta.get('mq') or '—'} mq"),
                 ('Occupazione', asta.get('occupazione') or '—'),
                 ('Rischio AI', f"{rischio}/10" if rischio else '—'),
                 ('Valore perizia', f"€{float(asta['valore_perizia']):,.0f}" if asta.get('valore_perizia') else '—'),
                 ('Data vendita', (asta.get('data_vendita') or '')[:10] or '—'),
               ]
      )}
    </table>
    {f'<p style="color:#555;font-style:italic;font-size:13px">{asta.get("descrizione_sintetica","")}</p>'
     if asta.get("descrizione_sintetica") else ''}
    {f'<div style="margin:12px 0">{tags_html}</div>' if tags_html else ''}
    <a href="{asta.get('url_dettaglio','#')}" style="display:inline-block;background:#c9a84c;
      color:#fff;padding:10px 20px;border-radius:5px;text-decoration:none;font-weight:bold">
      Vedi sul PVP →
    </a>
  </div>
  <p style="font-size:11px;color:#aaa;text-align:center;margin-top:12px">
    PVP Monitor · Regola: "{rule_nome}"
  </p>
</div>"""

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": body},
            )
            r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)
