"""
Interfaccia chat — motore di query in linguaggio naturale sul database aste.

L'utente fa una domanda → Claude decide quali filtri applicare
→ query Supabase → risposta con i risultati in formato leggibile.

Uso standalone (test):
    python interface/query_engine.py "Quali aste a Milano hanno abusi edilizi?"

Uso come modulo:
    from interface.query_engine import answer_question
    result = await answer_question("Aste libere con prezzo sotto 100k in Sicilia")
"""
import asyncio
import json
import logging
import os
import sys
from typing import Optional

import anthropic

from db.client import get_supabase

log = logging.getLogger("query")

MODEL = "claude-sonnet-4-20250514"


# ──────────────────────────────────────────────────────────────────────────
# Strumenti disponibili a Claude per interrogare il DB
# ──────────────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "cerca_aste",
        "description": (
            "Cerca aste nel database usando filtri strutturati. "
            "Usa questo tool per domande specifiche su province, prezzi, problemi, ecc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "provincia":          {"type": "string",  "description": "Sigla provincia (es: MI, RM, NA)"},
                "regione":            {"type": "string",  "description": "Nome regione (es: Lombardia)"},
                "comune":             {"type": "string",  "description": "Nome comune"},
                "prezzo_max":         {"type": "number",  "description": "Prezzo base massimo in euro"},
                "prezzo_min":         {"type": "number",  "description": "Prezzo base minimo in euro"},
                "mq_min":             {"type": "number",  "description": "Superficie minima in mq"},
                "mq_max":             {"type": "number",  "description": "Superficie massima in mq"},
                "tipologia":          {"type": "string",  "description": "Tipo immobile (es: appartamento, villa, negozio)"},
                "occupazione":        {"type": "string",  "description": "Libero | Occupato"},
                "abuso_edilizio":     {"type": "boolean", "description": "Filtra aste con/senza abusi edilizi"},
                "ipoteca_presente":   {"type": "boolean", "description": "Filtra aste con/senza ipoteche"},
                "occupato_terzi":     {"type": "boolean", "description": "Filtra aste occupate da terzi"},
                "spese_condominiali_arretrate": {"type": "boolean"},
                "difformita_catastale":        {"type": "boolean"},
                "necessita_ristrutturazione":  {"type": "boolean"},
                "amianto_presente":            {"type": "boolean"},
                "rischio_max":        {"type": "integer", "description": "Punteggio rischio massimo (1-10)"},
                "rischio_min":        {"type": "integer", "description": "Punteggio rischio minimo (1-10)"},
                "problema":           {"type": "string",  "description": "Tag problema specifico (es: vincolo_paesaggistico)"},
                "data_da":            {"type": "string",  "description": "Data pubblicazione da (YYYY-MM-DD)"},
                "limit":              {"type": "integer", "description": "Numero massimo risultati (default 10, max 50)"},
                "ordina_per":         {"type": "string",  "description": "prezzo_asc | prezzo_desc | data_desc | rischio_asc | rischio_desc"},
            },
        },
    },
    {
        "name": "statistiche",
        "description": "Ottieni statistiche aggregate: totale aste, prezzi medi, distribuzione per provincia ecc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gruppo_per": {
                    "type": "string",
                    "enum": ["provincia", "regione", "tipologia", "occupazione", "tribunale"],
                    "description": "Raggruppa le statistiche per questo campo",
                },
                "solo_con_analisi": {
                    "type": "boolean",
                    "description": "Solo aste con analisi AI completata",
                },
            },
        },
    },
    {
        "name": "dettaglio_asta",
        "description": "Ottieni tutti i dettagli di una singola asta dato il suo pvp_id o url.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pvp_id": {"type": "string"},
            },
            "required": ["pvp_id"],
        },
    },
]


# ──────────────────────────────────────────────────────────────────────────
# Esecuzione tool calls → query Supabase
# ──────────────────────────────────────────────────────────────────────────
def execute_tool(tool_name: str, tool_input: dict) -> dict:
    sb = get_supabase()

    if tool_name == "cerca_aste":
        return _cerca_aste(sb, tool_input)
    elif tool_name == "statistiche":
        return _statistiche(sb, tool_input)
    elif tool_name == "dettaglio_asta":
        return _dettaglio_asta(sb, tool_input)
    else:
        return {"error": f"Tool sconosciuto: {tool_name}"}


def _cerca_aste(sb, params: dict) -> dict:
    limit = min(params.get("limit", 10), 50)
    ordina = params.get("ordina_per", "data_desc")

    q = sb.table("v_aste_complete").select(
        "pvp_id,url_dettaglio,tribunale,comune,provincia,tipologia,"
        "prezzo_base,mq,occupazione,data_pubblicazione,data_vendita,"
        "abuso_edilizio,ipoteca_presente,occupato_terzi,spese_condominiali_arretrate,"
        "difformita_catastale,necessita_ristrutturazione,amianto_presente,"
        "punteggio_rischio,problemi_rilevati,descrizione_sintetica"
    )

    # Filtri geografici
    if params.get("provincia"):
        q = q.ilike("provincia", f"%{params['provincia']}%")
    if params.get("regione"):
        q = q.ilike("regione", f"%{params['regione']}%")
    if params.get("comune"):
        q = q.ilike("comune", f"%{params['comune']}%")

    # Filtri prezzo
    if params.get("prezzo_min") is not None:
        q = q.gte("prezzo_base", params["prezzo_min"])
    if params.get("prezzo_max") is not None:
        q = q.lte("prezzo_base", params["prezzo_max"])

    # Filtri superficie
    if params.get("mq_min") is not None:
        q = q.gte("mq", params["mq_min"])
    if params.get("mq_max") is not None:
        q = q.lte("mq", params["mq_max"])

    # Filtri tipo
    if params.get("tipologia"):
        q = q.ilike("tipologia", f"%{params['tipologia']}%")
    if params.get("occupazione"):
        q = q.ilike("occupazione", f"%{params['occupazione']}%")

    # Filtri problemi (booleani)
    for field in [
        "abuso_edilizio", "ipoteca_presente", "occupato_terzi",
        "spese_condominiali_arretrate", "difformita_catastale",
        "necessita_ristrutturazione", "amianto_presente",
    ]:
        if params.get(field) is not None:
            q = q.eq(field, params[field])

    # Filtro rischio
    if params.get("rischio_min") is not None:
        q = q.gte("punteggio_rischio", params["rischio_min"])
    if params.get("rischio_max") is not None:
        q = q.lte("punteggio_rischio", params["rischio_max"])

    # Filtro tag problema
    if params.get("problema"):
        q = q.contains("problemi_rilevati", [params["problema"]])

    # Filtro data
    if params.get("data_da"):
        q = q.gte("data_pubblicazione", params["data_da"])

    # Ordinamento
    order_map = {
        "prezzo_asc":   ("prezzo_base", False),
        "prezzo_desc":  ("prezzo_base", True),
        "data_desc":    ("data_pubblicazione", True),
        "rischio_asc":  ("punteggio_rischio", False),
        "rischio_desc": ("punteggio_rischio", True),
    }
    col, desc = order_map.get(ordina, ("data_pubblicazione", True))
    q = q.order(col, desc=desc)

    res = q.limit(limit).execute()
    return {"risultati": res.data or [], "count": len(res.data or [])}


def _statistiche(sb, params: dict) -> dict:
    gruppo = params.get("gruppo_per", "provincia")
    # Query aggregata via RPC o view
    res = sb.table("v_aste_complete").select(
        f"{gruppo},prezzo_base,punteggio_rischio,abuso_edilizio,occupato_terzi"
    ).execute()

    from collections import defaultdict
    groups = defaultdict(lambda: {
        "count": 0, "prezzi": [], "rischi": [],
        "abusi": 0, "occupati": 0
    })

    for row in (res.data or []):
        key = row.get(gruppo) or "N/D"
        g = groups[key]
        g["count"] += 1
        if row.get("prezzo_base"):
            g["prezzi"].append(row["prezzo_base"])
        if row.get("punteggio_rischio"):
            g["rischi"].append(row["punteggio_rischio"])
        if row.get("abuso_edilizio"):
            g["abusi"] += 1
        if row.get("occupato_terzi"):
            g["occupati"] += 1

    stats = []
    for key, g in sorted(groups.items(), key=lambda x: -x[1]["count"])[:20]:
        prezzi = g["prezzi"]
        rischi = g["rischi"]
        stats.append({
            gruppo: key,
            "totale_aste": g["count"],
            "prezzo_medio": round(sum(prezzi) / len(prezzi)) if prezzi else None,
            "prezzo_min": round(min(prezzi)) if prezzi else None,
            "prezzo_max": round(max(prezzi)) if prezzi else None,
            "rischio_medio": round(sum(rischi) / len(rischi), 1) if rischi else None,
            "con_abuso_edilizio": g["abusi"],
            "occupate_da_terzi": g["occupati"],
        })

    return {"statistiche": stats, "raggruppate_per": gruppo}


def _dettaglio_asta(sb, params: dict) -> dict:
    pvp_id = params["pvp_id"]
    res = sb.table("v_aste_complete").select("*").eq("pvp_id", pvp_id).execute()
    if not res.data:
        return {"error": f"Asta {pvp_id} non trovata"}
    asta = res.data[0]

    # Aggiungi documenti
    docs = sb.table("documenti").select(
        "nome_file,tipo,url_originale,scaricato,num_pagine,dimensione_kb"
    ).eq("asta_id", asta["id"]).execute()
    asta["documenti"] = docs.data or []

    return asta


# ──────────────────────────────────────────────────────────────────────────
# Query engine principale (agentic loop)
# ──────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Sei un assistente esperto in aste giudiziarie immobiliari italiane.
Hai accesso a un database aggiornato quotidianamente con tutte le aste pubblicate sul PVP (Portale delle Vendite Pubbliche).

Per ogni domanda:
1. Usa i tool disponibili per interrogare il database
2. Interpreta i risultati in modo chiaro e utile
3. Segnala sempre quante aste hai trovato
4. Formatta i risultati in modo leggibile: per ogni asta mostra pvp_id, comune, prezzo, problemi rilevati
5. Suggerisci filtri aggiuntivi utili se i risultati sono troppi o troppo pochi
6. Parla italiano"""


async def answer_question(domanda: str) -> str:
    """
    Riceve una domanda in linguaggio naturale e restituisce la risposta.
    Usa Claude come orchestratore: decide quali tool usare e come presentare i risultati.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages = [{"role": "user", "content": domanda}]

    # Agentic loop: Claude può fare più tool calls consecutive
    for _ in range(5):  # max 5 round
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Aggiungi risposta alla storia
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "end_turn":
            # Risposta finale — estrai testo
            for block in resp.content:
                if block.type == "text":
                    return block.text
            return "Nessuna risposta generata."

        if resp.stop_reason == "tool_use":
            # Esegui tutti i tool calls richiesti
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    log.info(f"Tool: {block.name} | Input: {json.dumps(block.input, ensure_ascii=False)[:200]}")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })

            messages.append({"role": "user", "content": tool_results})
            continue

        break

    return "Errore: impossibile completare la query."


# ──────────────────────────────────────────────────────────────────────────
# Entry point CLI per test
# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Uso: python interface/query_engine.py \"La tua domanda\"")
        sys.exit(1)

    domanda = " ".join(sys.argv[1:])
    print(f"\nDomanda: {domanda}\n")
    print("=" * 60)
    risposta = asyncio.run(answer_question(domanda))
    print(risposta)
