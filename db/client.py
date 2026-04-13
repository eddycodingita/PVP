"""
Client Supabase — tutte le operazioni DB.
Include: storico prezzi (#5), reconciler (#4), alert (#7),
         chat sessions (#8), costi run (#10), refresh view (#12).
"""
import os, logging
from datetime import datetime
from typing import Optional
from supabase import create_client, Client

log = logging.getLogger("db")
_client: Optional[Client] = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _client


# ── ASTE ──────────────────────────────────────────────────────────────────
def db_asta_exists(pvp_id: str) -> bool:
    res = get_supabase().table("aste").select("id").eq("pvp_id", pvp_id).limit(1).execute()
    return bool(res.data)


def db_get_asta(pvp_id: str) -> Optional[dict]:
    res = get_supabase().table("aste").select("id,prezzo_base").eq("pvp_id", pvp_id).limit(1).execute()
    return res.data[0] if res.data else None


def db_upsert_asta(detail: dict) -> str:
    """Upsert asta. Detecta variazione prezzo (#5). Restituisce id UUID."""
    sb = get_supabase()
    docs = detail.pop("documenti", []) or []

    record = {k: v for k, v in {
        "pvp_id":             detail.get("pvp_id"),
        "url_dettaglio":      detail.get("url_dettaglio"),
        "titolo":             detail.get("titolo"),
        "tribunale":          detail.get("tribunale"),
        "numero_procedura":   detail.get("numero_procedura"),
        "lotto":              detail.get("lotto"),
        "tipo_asta":          detail.get("tipo_asta"),
        "tipologia":          detail.get("tipologia"),
        "indirizzo":          detail.get("indirizzo"),
        "comune":             detail.get("comune"),
        "provincia":          detail.get("provincia"),
        "regione":            detail.get("regione"),
        "cap":                detail.get("cap"),
        "latitudine":         _float(detail.get("latitudine")),
        "longitudine":        _float(detail.get("longitudine")),
        "mq":                 detail.get("mq"),
        "vani":               detail.get("vani"),
        "piano":              detail.get("piano"),
        "nr_locali":          detail.get("nr_locali"),
        "nr_bagni":           detail.get("nr_bagni"),
        "nr_posti_auto":      detail.get("nr_posti_auto"),
        "foglio":             detail.get("foglio"),
        "particella":         detail.get("particella"),
        "subalterno":         detail.get("subalterno"),
        "occupazione":        detail.get("occupazione"),
        "prezzo_base":        detail.get("prezzo_base"),
        "offerta_minima":     detail.get("offerta_minima"),
        "rialzo_minimo":      detail.get("rialzo_minimo"),
        "tipo_vendita":       detail.get("tipo_vendita"),
        "modalita_vendita":   detail.get("modalita_vendita"),
        "data_vendita":       detail.get("data_vendita"),
        "data_pubblicazione": detail.get("data_pubblicazione"),
        "data_scadenza":      detail.get("data_scadenza"),
        "giudice":            detail.get("giudice"),
        "delegato":           detail.get("delegato"),
        "custode":            detail.get("custode"),
        "custode_email":      detail.get("custode_email"),
        "custode_tel":        detail.get("custode_tel"),
        "descrizione":        detail.get("descrizione"),
        "is_active":          True,
        "scraped_at":         datetime.utcnow().isoformat(),
    }.items() if v is not None}

    # #5 — variazione prezzo
    pvp_id      = record.get("pvp_id")
    nuovo_prezzo = record.get("prezzo_base")
    if pvp_id and nuovo_prezzo:
        existing = db_get_asta(pvp_id)
        if existing and existing.get("prezzo_base"):
            old = float(existing["prezzo_base"])
            new = float(nuovo_prezzo)
            if abs(old - new) > 1:
                pct = round((new - old) / old * 100, 2)
                log.info(f"  💰 Ribasso {pvp_id}: €{old:,.0f}→€{new:,.0f} ({pct:+.1f}%)")
                db_log_price_change(existing["id"], pvp_id, old, new, pct)

    res = sb.table("aste").upsert(record, on_conflict="pvp_id").execute()
    asta_id = res.data[0]["id"]
    detail["documenti"] = docs
    return asta_id


# ── #5 Storico prezzi ─────────────────────────────────────────────────────
def db_log_price_change(asta_id, pvp_id, old_p, new_p, pct):
    get_supabase().table("aste_prezzi_history").insert({
        "asta_id": asta_id, "pvp_id": pvp_id,
        "prezzo_base_old": old_p, "prezzo_base_new": new_p, "variazione_pct": pct,
    }).execute()


# ── DOCUMENTI ─────────────────────────────────────────────────────────────
def db_insert_documento(asta_id: str, doc: dict):
    try:
        get_supabase().table("documenti").upsert({
            "asta_id": asta_id, "nome_file": doc.get("nome"),
            "tipo": doc.get("tipo", "allegato"), "url_originale": doc.get("url"),
            "scaricato": False, "analizzato": False,
        }, on_conflict="asta_id,url_originale").execute()
    except Exception as e:
        log.warning(f"Insert documento: {e}")


def db_get_documenti_da_scaricare() -> list[dict]:
    res = (get_supabase().table("documenti")
           .select("id,asta_id,url_originale,nome_file,tipo")
           .eq("scaricato", False).is_("errore", "null").limit(500).execute())
    return res.data or []


def db_update_documento(doc_id: str, fields: dict):
    get_supabase().table("documenti").update(fields).eq("id", doc_id).execute()


# ── ANALISI AI ────────────────────────────────────────────────────────────
def db_get_aste_da_analizzare() -> list[dict]:
    sb = get_supabase()
    aste = (sb.table("aste").select("id,pvp_id,tribunale,tipologia,occupazione,comune,prezzo_base,descrizione")
            .eq("is_active", True).order("scraped_at", desc=True).limit(200).execute().data or [])
    if not aste:
        return []
    ids = [a["id"] for a in aste]
    analizzate = {r["asta_id"] for r in (
        sb.table("analisi_ai").select("asta_id").in_("asta_id", ids).execute().data or [])}
    return [a for a in aste if a["id"] not in analizzate]


def db_get_documenti_di_asta(asta_id: str) -> list[dict]:
    res = (get_supabase().table("documenti")
           .select("id,url_originale,nome_file,tipo,testo_estratto")
           .eq("asta_id", asta_id).eq("scaricato", True).limit(10).execute())
    return res.data or []


def db_save_analisi(analisi: dict):
    get_supabase().table("analisi_ai").upsert(analisi, on_conflict="asta_id").execute()


def db_mark_documenti_analizzati(asta_id: str):
    get_supabase().table("documenti").update({"analizzato": True}).eq("asta_id", asta_id).execute()


# ── #4 Reconciler ─────────────────────────────────────────────────────────
def db_get_all_active_pvp_ids() -> list[str]:
    res = get_supabase().table("aste").select("pvp_id").eq("is_active", True).execute()
    return [r["pvp_id"] for r in (res.data or [])]


def db_mark_inactive(pvp_ids: list[str]):
    if not pvp_ids:
        return
    get_supabase().table("aste").update({"is_active": False}).in_("pvp_id", pvp_ids).execute()
    log.info(f"  Marcate inattive: {len(pvp_ids)} aste")


# ── #7 Alert ──────────────────────────────────────────────────────────────
def db_get_active_alert_rules() -> list[dict]:
    return get_supabase().table("alert_rules").select("*").eq("attivo", True).execute().data or []


def db_get_aste_nuove_per_alert(da: str) -> list[dict]:
    return (get_supabase().table("v_aste_complete")
            .select("*").gte("scraped_at", da).execute().data or [])


def db_alert_gia_inviato(rule_id: str, asta_id: str) -> bool:
    res = (get_supabase().table("alert_log").select("id")
           .eq("rule_id", rule_id).eq("asta_id", asta_id).limit(1).execute())
    return bool(res.data)


def db_log_alert(rule_id, asta_id, pvp_id, canale, ok, err=None):
    get_supabase().table("alert_log").insert({
        "rule_id": rule_id, "asta_id": asta_id, "pvp_id": pvp_id,
        "canale": canale, "successo": ok, "errore": err,
    }).execute()


def db_update_alert_timestamp(rule_id: str):
    get_supabase().table("alert_rules").update(
        {"ultima_esecuzione": datetime.utcnow().isoformat()}
    ).eq("id", rule_id).execute()


# ── #8 Chat sessions ──────────────────────────────────────────────────────
def db_get_sessions(limit: int = 30) -> list[dict]:
    return (get_supabase().table("chat_sessions")
            .select("id,titolo,aggiornata_il")
            .order("aggiornata_il", desc=True).limit(limit).execute().data or [])


def db_get_session(session_id: str) -> Optional[dict]:
    res = get_supabase().table("chat_sessions").select("*").eq("id", session_id).limit(1).execute()
    return res.data[0] if res.data else None


def db_create_session(titolo: str, messages: list) -> str:
    res = get_supabase().table("chat_sessions").insert(
        {"titolo": titolo, "messages": messages}
    ).execute()
    return res.data[0]["id"]


def db_update_session(session_id: str, messages: list, titolo: str = None):
    fields: dict = {"messages": messages}
    if titolo:
        fields["titolo"] = titolo
    get_supabase().table("chat_sessions").update(fields).eq("id", session_id).execute()


def db_delete_session(session_id: str):
    get_supabase().table("chat_sessions").delete().eq("id", session_id).execute()


# ── #10 Scraping runs ─────────────────────────────────────────────────────
def db_log_run_start(mode: str) -> str:
    res = get_supabase().table("scraping_runs").insert({
        "status": "running", "mode": mode,
        "started_at": datetime.utcnow().isoformat(),
    }).execute()
    return res.data[0]["id"]


def db_log_run_end(run_id: str, status: str, stats: dict, note: str = None):
    get_supabase().table("scraping_runs").update({
        "status":               status,
        "ended_at":             datetime.utcnow().isoformat(),
        "aste_scraped":         stats.get("scraped", 0),
        "aste_nuove":           stats.get("new", 0),
        "aste_errori":          stats.get("errors", 0),
        "docs_trovati":         stats.get("downloaded", 0),
        "aste_analizzate":      stats.get("analyzed", 0),
        "costo_stimato_eur":    round(stats.get("costo_eur", 0.0), 4),
        "tokens_input_totali":  stats.get("tokens_in", 0),
        "tokens_output_totali": stats.get("tokens_out", 0),
        "note":                 note,
    }).eq("id", run_id).execute()


# ── #12 Materialized view refresh ─────────────────────────────────────────
def db_refresh_materialized_view():
    try:
        get_supabase().rpc("refresh_mv_aste").execute()
        log.info("  ✓ Materialized view refreshata")
    except Exception as e:
        log.warning(f"  Refresh view: {e}")


def _float(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None
