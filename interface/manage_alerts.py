"""
CLI per gestire le regole di alert senza scrivere SQL.

Uso:
    python interface/manage_alerts.py list
    python interface/manage_alerts.py add
    python interface/manage_alerts.py toggle <id>
    python interface/manage_alerts.py delete <id>
    python interface/manage_alerts.py test <id>   # test invio immediato
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.env_check import load_dotenv, check as check_env

load_dotenv()
check_env(exit_on_error=True)

from db.client import get_supabase

sb = get_supabase()


def cmd_list():
    res = sb.table("alert_rules").select("*").order("creato_il", desc=True).execute()
    rules = res.data or []
    if not rules:
        print("Nessuna regola configurata.")
        print("Usa: python manage_alerts.py add")
        return

    print(f"\n{'─'*70}")
    print(f"{'NOME':<25} {'CANALE':<10} {'STATO':<8} {'DESTINATARIO':<30}")
    print(f"{'─'*70}")
    for r in rules:
        stato = "✅ attiva" if r["attivo"] else "⏸ pausa"
        dest  = r.get("destinatario", "")[:28]
        print(f"{r['nome'][:24]:<25} {r.get('canale',''):<10} {stato:<8} {dest:<30}")
        print(f"  ID: {r['id']}")
        filtri = []
        if r.get("provincia"):    filtri.append(f"prov={r['provincia']}")
        if r.get("regione"):      filtri.append(f"reg={r['regione']}")
        if r.get("comune"):       filtri.append(f"comune={r['comune']}")
        if r.get("prezzo_max"):   filtri.append(f"max=€{r['prezzo_max']:,.0f}")
        if r.get("prezzo_min"):   filtri.append(f"min=€{r['prezzo_min']:,.0f}")
        if r.get("mq_min"):       filtri.append(f"mq≥{r['mq_min']}")
        if r.get("tipologia"):    filtri.append(f"tipo={r['tipologia']}")
        if r.get("solo_libere"):  filtri.append("solo_libere")
        if r.get("rischio_max"):  filtri.append(f"rischio≤{r['rischio_max']}")
        if r.get("problemi_esclusi"): filtri.append(f"escludi={r['problemi_esclusi']}")
        if filtri:
            print(f"  Filtri: {' | '.join(filtri)}")
        ts = r.get("ultima_esecuzione")
        print(f"  Ultima esecuzione: {ts[:16] if ts else 'mai'}")
        print()


def cmd_add():
    print("\n🔔 Crea nuova regola di alert")
    print("Lascia vuoto per saltare un filtro.\n")

    nome = input("Nome regola (es: 'Appartamenti Milano liberi'): ").strip()
    if not nome:
        print("Nome obbligatorio.")
        return

    print("\n── Filtri geografici ─────────────────────────────────")
    provincia = input("Provincia (sigla, es: MI) [opz]: ").strip() or None
    regione   = input("Regione (es: Lombardia) [opz]: ").strip() or None
    comune    = input("Comune [opz]: ").strip() or None

    print("\n── Filtri prezzo ─────────────────────────────────────")
    pmax = _input_num("Prezzo massimo €", None)
    pmin = _input_num("Prezzo minimo €", None)

    print("\n── Filtri immobile ───────────────────────────────────")
    mq_min    = _input_num("Superficie minima mq", None)
    tipologia = input("Tipologia (es: appartamento) [opz]: ").strip() or None

    solo_libere_str = input("Solo immobili liberi? [s/N]: ").strip().lower()
    solo_libere = solo_libere_str in ("s", "si", "sì", "y", "yes")

    rischio_max_str = input("Punteggio rischio massimo (1-10) [opz]: ").strip()
    rischio_max = int(rischio_max_str) if rischio_max_str.isdigit() else None

    print("\n── Problemi da escludere ─────────────────────────────")
    print("Tag disponibili: abuso_edilizio, ipoteca_rilevante, occupato_coattivo,")
    print("  locazione_in_corso, amianto, ristrutturazione_totale, difformita_catastale")
    escludi_str = input("Escludi (separati da virgola) [opz]: ").strip()
    problemi_esclusi = [p.strip() for p in escludi_str.split(",") if p.strip()] or None

    print("\n── Notifiche ─────────────────────────────────────────")
    canale = ""
    while canale not in ("email", "telegram"):
        canale = input("Canale (email / telegram): ").strip().lower()

    destinatario = input(
        "Indirizzo email: " if canale == "email" else "Chat ID Telegram: "
    ).strip()

    # Mostra riepilogo
    print("\n── Riepilogo ─────────────────────────────────────────")
    print(f"  Nome:         {nome}")
    print(f"  Provincia:    {provincia or '—'}")
    print(f"  Regione:      {regione or '—'}")
    print(f"  Comune:       {comune or '—'}")
    print(f"  Prezzo:       {f'€{pmin:,.0f}' if pmin else '—'} – {f'€{pmax:,.0f}' if pmax else '—'}")
    print(f"  Superficie:   ≥{mq_min} mq" if mq_min else "  Superficie:   —")
    print(f"  Tipologia:    {tipologia or '—'}")
    print(f"  Solo libere:  {'Sì' if solo_libere else 'No'}")
    print(f"  Rischio max:  {rischio_max or '—'}")
    print(f"  Escludi:      {problemi_esclusi or '—'}")
    print(f"  Canale:       {canale} → {destinatario}")

    ok = input("\nConfermi? [S/n]: ").strip().lower()
    if ok == "n":
        print("Annullato.")
        return

    record = {
        "nome":             nome,
        "attivo":           True,
        "canale":           canale,
        "destinatario":     destinatario,
        "provincia":        provincia,
        "regione":          regione,
        "comune":           comune,
        "prezzo_max":       pmax,
        "prezzo_min":       pmin,
        "mq_min":           mq_min,
        "tipologia":        tipologia,
        "solo_libere":      solo_libere,
        "rischio_max":      rischio_max,
        "problemi_esclusi": problemi_esclusi,
    }
    record = {k: v for k, v in record.items() if v is not None or k in ("attivo",)}

    res = sb.table("alert_rules").insert(record).execute()
    rule_id = res.data[0]["id"]
    print(f"\n✅ Regola creata! ID: {rule_id}")
    print("Verrà eseguita alla prossima run della pipeline.")


def cmd_toggle(rule_id: str):
    res = sb.table("alert_rules").select("nome,attivo").eq("id", rule_id).single().execute()
    if not res.data:
        print(f"Regola {rule_id} non trovata.")
        return
    r = res.data
    nuovo = not r["attivo"]
    sb.table("alert_rules").update({"attivo": nuovo}).eq("id", rule_id).execute()
    stato = "attivata ✅" if nuovo else "messa in pausa ⏸"
    print(f"Regola '{r['nome']}' {stato}.")


def cmd_delete(rule_id: str):
    res = sb.table("alert_rules").select("nome").eq("id", rule_id).single().execute()
    if not res.data:
        print(f"Regola {rule_id} non trovata.")
        return
    nome = res.data["nome"]
    ok = input(f"Elimina '{nome}'? [s/N]: ").strip().lower()
    if ok not in ("s", "si", "sì", "y"):
        print("Annullato.")
        return
    sb.table("alert_rules").delete().eq("id", rule_id).execute()
    print(f"Regola '{nome}' eliminata.")


def cmd_test(rule_id: str):
    """Invia un alert di test usando l'ultima asta disponibile."""
    rule_res = sb.table("alert_rules").select("*").eq("id", rule_id).single().execute()
    if not rule_res.data:
        print(f"Regola {rule_id} non trovata.")
        return
    rule = rule_res.data

    # Prende la prima asta disponibile come test
    asta_res = (
        sb.table("aste")
        .select("id,pvp_id,url_dettaglio,tribunale,comune,provincia,tipologia,prezzo_base,mq,occupazione")
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not asta_res.data:
        print("Nessuna asta disponibile per il test.")
        return

    asta = asta_res.data[0]
    asta.update({"punteggio_rischio": 5, "problemi_rilevati": ["test_alert"],
                 "descrizione_sintetica": "Questo è un alert di test da PVP Monitor.",
                 "valore_perizia": None, "sconto_perizia_pct": None})

    print(f"Invio alert di test a {rule['destinatario']} via {rule['canale']}...")

    from interface.alerts import _send_telegram, _send_email

    async def _run():
        if rule["canale"] == "telegram":
            ok, err = await _send_telegram(rule["destinatario"], asta)
        else:
            ok, err = await _send_email(rule["destinatario"], rule["nome"], asta)
        if ok:
            print("✅ Alert di test inviato con successo!")
        else:
            print(f"❌ Invio fallito: {err}")

    asyncio.run(_run())


# ── Helpers ───────────────────────────────────────────────────────────
def _input_num(label, default):
    v = input(f"{label} [opz]: ").strip()
    if not v:
        return default
    try:
        return float(v.replace(".", "").replace(",", "."))
    except ValueError:
        return default


# ── Entry point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
    elif args[0] == "add":
        cmd_add()
    elif args[0] == "toggle" and len(args) > 1:
        cmd_toggle(args[1])
    elif args[0] == "delete" and len(args) > 1:
        cmd_delete(args[1])
    elif args[0] == "test" and len(args) > 1:
        cmd_test(args[1])
    else:
        print("Comandi: list | add | toggle <id> | delete <id> | test <id>")
