"""
STEP 4: Analisi AI dei documenti con Claude.

#6  — Estrae valore perizia e calcola sconto rispetto al prezzo base.
#10 — Logga token e costo stimato per ogni analisi.
"""
import asyncio, json, logging, os
from typing import Optional
import anthropic
from db.client import (
    db_get_aste_da_analizzare, db_get_documenti_di_asta,
    db_save_analisi, db_mark_documenti_analizzati,
)

log = logging.getLogger("analyzer")

MODEL          = "claude-sonnet-4-20250514"
MAX_CHARS_DOC  = 12_000
MAX_DOCS       = 5
RATE_DELAY     = 1.0

# #10 — Prezzi Claude Sonnet (USD per milione di token, aggiornare se cambiano)
PRICE_IN_PER_MTK  = 3.0   # input
PRICE_OUT_PER_MTK = 15.0  # output


SYSTEM = """Sei un esperto analista di aste giudiziarie immobiliari italiane.
Leggi perizie e documenti allegati con precisione professionale.
Rispondi SOLO con JSON valido — nessun testo o markdown aggiuntivo."""

PROMPT = """Analizza i documenti di questa asta e rispondi con questo JSON esatto:

{
  "abuso_edilizio": true/false/null,
  "abuso_edilizio_note": "",
  "sanatoria_pendente": true/false/null,
  "vincoli_urbanistici": true/false/null,
  "vincoli_note": "",
  "difformita_catastale": true/false/null,
  "difformita_note": "",
  "ipoteca_presente": true/false/null,
  "ipoteca_importo": null,
  "ipoteca_note": "",
  "spese_condominiali_arretrate": true/false/null,
  "spese_condominiali_importo": null,
  "debiti_fiscali": true/false/null,
  "debiti_note": "",
  "occupato_terzi": true/false/null,
  "contratto_locazione": true/false/null,
  "contratto_scadenza": null,
  "occupazione_note": "",
  "necessita_ristrutturazione": true/false/null,
  "livello_ristrutturazione": null,
  "amianto_presente": true/false/null,
  "danni_strutturali": true/false/null,
  "anno_costruzione": null,
  "stato_conservazione": null,
  "classe_energetica": null,
  "impianti_conformi": true/false/null,
  "certificato_agibilita": true/false/null,
  "valore_perizia": null,
  "descrizione_sintetica": "",
  "problemi_rilevati": [],
  "punteggio_rischio": 1,
  "note_aggiuntive": ""
}

REGOLE:
- valore_perizia: il valore stimato nella perizia tecnica (numero, in euro). null se non trovato.
- punteggio_rischio: 1 (nessun problema) → 10 (problemi gravi e multipli)
- problemi_rilevati tag ammessi: abuso_edilizio, sanatoria_pendente, vincolo_paesaggistico,
  vincolo_idrogeologico, difformita_catastale, ipoteca_rilevante, spese_condominiali_arretrate,
  debiti_fiscali, occupato_coattivo, locazione_in_corso, locazione_scaduta,
  ristrutturazione_totale, ristrutturazione_parziale, amianto, danni_strutturali,
  assenza_agibilita, impianti_non_conformi
- descrizione_sintetica: 2-3 frasi chiare, come spiegheresti a un investitore cosa sta guardando
- Segnala un problema solo se ESPLICITAMENTE menzionato nel documento
- null = informazione non presente nel documento

DATI ASTA:
Tribunale: {tribunale}
Comune: {comune} ({provincia})
Tipologia: {tipologia}
Occupazione: {occupazione}
Prezzo base: €{prezzo_base}

DOCUMENTI:
{testo_documenti}"""


class AstaAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.stats  = {"analyzed": 0, "errors": 0, "no_docs": 0,
                       "tokens_in": 0, "tokens_out": 0, "costo_eur": 0.0}

    async def run_all_pending(self) -> dict:
        aste = db_get_aste_da_analizzare()
        log.info(f"Aste da analizzare: {len(aste)}")
        for asta in aste:
            await self.analyze_asta(asta)
            await asyncio.sleep(RATE_DELAY)
        return self.stats

    async def analyze_asta(self, asta: dict):
        asta_id = asta["id"]
        pvp_id  = asta["pvp_id"]
        try:
            docs = db_get_documenti_di_asta(asta_id)
            testo_docs = _build_doc_text(docs)

            if not testo_docs.strip():
                self.stats["no_docs"] += 1
                testo_docs = "(Nessun documento testuale disponibile)"

            prompt = PROMPT.format(
                tribunale=asta.get("tribunale") or "N/D",
                comune=asta.get("comune") or "N/D",
                provincia=asta.get("provincia") or "",
                tipologia=asta.get("tipologia") or "N/D",
                occupazione=asta.get("occupazione") or "N/D",
                prezzo_base=f"{float(asta['prezzo_base']):,.0f}" if asta.get("prezzo_base") else "N/D",
                testo_documenti=testo_docs,
            )

            # Claude API (run in executor per non bloccare l'event loop)
            msg = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.client.messages.create(
                    model=MODEL, max_tokens=1500, system=SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
            )

            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            analisi = json.loads(raw.strip())

            # #6 — Calcola sconto perizia
            valore_perizia = analisi.get("valore_perizia")
            prezzo_base    = asta.get("prezzo_base")
            if valore_perizia and prezzo_base and float(valore_perizia) > 0:
                sconto = round((float(valore_perizia) - float(prezzo_base)) / float(valore_perizia) * 100, 1)
                analisi["sconto_perizia_pct"] = sconto
                log.info(f"    📉 Sconto perizia: {sconto}% (perizia €{valore_perizia:,.0f} vs base €{prezzo_base:,.0f})")

            analisi["asta_id"]        = asta_id
            analisi["modello_ai"]     = MODEL
            analisi["tokens_input"]   = msg.usage.input_tokens
            analisi["tokens_output"]  = msg.usage.output_tokens

            # #10 — Accumula costo
            costo_run = _calc_cost(msg.usage.input_tokens, msg.usage.output_tokens)
            self.stats["tokens_in"]  += msg.usage.input_tokens
            self.stats["tokens_out"] += msg.usage.output_tokens
            self.stats["costo_eur"]  += costo_run

            # Embedding (opzionale)
            embed_text = f"{analisi.get('descrizione_sintetica','')} {' '.join(analisi.get('problemi_rilevati') or [])}"
            if embed_text.strip():
                analisi["embedding"] = await _embed(embed_text)

            db_save_analisi(analisi)
            db_mark_documenti_analizzati(asta_id)

            log.info(
                f"  ✓ {pvp_id} | rischio={analisi.get('punteggio_rischio')}/10 "
                f"| {analisi.get('problemi_rilevati')} | ${costo_run:.4f}"
            )
            self.stats["analyzed"] += 1

        except json.JSONDecodeError as e:
            log.error(f"  ✗ {pvp_id}: JSON malformato: {e}")
            self.stats["errors"] += 1
        except Exception as e:
            log.error(f"  ✗ {pvp_id}: {e}")
            self.stats["errors"] += 1


# ── Helpers ───────────────────────────────────────────────────────────────
def _build_doc_text(docs: list[dict]) -> str:
    parts = []
    for doc in docs[:MAX_DOCS]:
        testo = (doc.get("testo_estratto") or "").strip()
        if testo:
            tipo = doc.get("tipo", "documento")
            parts.append(f"=== {tipo.upper()} ===\n{testo[:MAX_CHARS_DOC]}")
    return "\n\n".join(parts)


def _calc_cost(tokens_in: int, tokens_out: int) -> float:
    """Costo stimato in EUR (tasso di cambio approssimativo USD→EUR 0.93)."""
    usd = (tokens_in / 1_000_000 * PRICE_IN_PER_MTK +
           tokens_out / 1_000_000 * PRICE_OUT_PER_MTK)
    return usd * 0.93


async def _embed(text: str) -> Optional[list[float]]:
    try:
        import openai
        oai  = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        resp = await oai.embeddings.create(model="text-embedding-3-small", input=text[:8000])
        return resp.data[0].embedding
    except Exception as e:
        log.warning(f"  Embedding: {e}")
        return None
