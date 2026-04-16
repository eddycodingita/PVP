import type { NextApiRequest, NextApiResponse } from 'next'
import Anthropic from '@anthropic-ai/sdk'
import { createClient } from '@supabase/supabase-js'

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! })
const supabase  = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
)

const TOOLS: Anthropic.Tool[] = [
  {
    name: 'cerca_aste',
    description: 'Cerca aste immobiliari nel database con filtri strutturati.',
    input_schema: {
      type: 'object',
      properties: {
        provincia:   { type: 'string' },
        regione:     { type: 'string' },
        comune:      { type: 'string' },
        prezzo_min:  { type: 'number' },
        prezzo_max:  { type: 'number' },
        mq_min:      { type: 'number' },
        mq_max:      { type: 'number' },
        tipologia:   { type: 'string' },
        occupazione: { type: 'string', description: 'Libero oppure Occupato' },
        abuso_edilizio:              { type: 'boolean' },
        ipoteca_presente:            { type: 'boolean' },
        occupato_terzi:              { type: 'boolean' },
        spese_condominiali_arretrate:{ type: 'boolean' },
        difformita_catastale:        { type: 'boolean' },
        necessita_ristrutturazione:  { type: 'boolean' },
        amianto_presente:            { type: 'boolean' },
        rischio_min:      { type: 'number' },
        rischio_max:      { type: 'number' },
        sconto_min:       { type: 'number', description: 'Sconto minimo su perizia in %' },
        solo_con_ribassi: { type: 'boolean' },
        problema:         { type: 'string' },
        data_da:          { type: 'string', description: 'YYYY-MM-DD' },
        ordina_per:       { type: 'string', description: 'prezzo_asc|prezzo_desc|data_desc|rischio_asc|rischio_desc|sconto_desc' },
        limit:            { type: 'number' },
      },
    },
  },
  {
    name: 'statistiche',
    description: 'Statistiche aggregate per provincia, regione, tipologia, ecc.',
    input_schema: {
      type: 'object',
      properties: {
        gruppo_per: { type: 'string', enum: ['provincia','regione','tipologia','occupazione','tribunale'] },
      },
    },
  },
  {
    name: 'dettaglio_asta',
    description: 'Dettaglio completo di una singola asta incluso storico prezzi e documenti.',
    input_schema: {
      type: 'object',
      properties: { pvp_id: { type: 'string' } },
      required: ['pvp_id'],
    },
  },
  {
    name: 'storico_prezzi',
    description: 'Aste che hanno subito ribassi di prezzo.',
    input_schema: {
      type: 'object',
      properties: {
        pvp_id: { type: 'string' },
        limit:  { type: 'number' },
      },
    },
  },
]

async function executeTool(name: string, input: Record<string, unknown>) {
  if (name === 'cerca_aste') {
    const limit  = Math.min(Number(input.limit ?? 8), 30)
    const ordina = String(input.ordina_per ?? 'data_desc')
    const orderMap: Record<string, [string, boolean]> = {
      prezzo_asc:   ['prezzo_base', false],
      prezzo_desc:  ['prezzo_base', true],
      data_desc:    ['data_pubblicazione', true],
      rischio_asc:  ['punteggio_rischio', false],
      rischio_desc: ['punteggio_rischio', true],
      sconto_desc:  ['sconto_perizia_pct', true],
    }
    const [col, desc] = orderMap[ordina] ?? ['data_pubblicazione', true]

    let q = supabase.from('v_aste_complete').select(
      'pvp_id,url_dettaglio,tribunale,comune,provincia,tipologia,' +
      'prezzo_base,prezzo_precedente,mq,occupazione,' +
      'data_pubblicazione,data_vendita,' +
      'abuso_edilizio,ipoteca_presente,occupato_terzi,' +
      'spese_condominiali_arretrate,difformita_catastale,' +
      'necessita_ristrutturazione,amianto_presente,' +
      'punteggio_rischio,problemi_rilevati,descrizione_sintetica,' +
      'valore_perizia,sconto_perizia_pct,num_ribassi,num_documenti'
    )

    if (input.provincia)   q = q.ilike('provincia', `%${input.provincia}%`)
    if (input.regione)     q = q.ilike('regione',   `%${input.regione}%`)
    if (input.comune)      q = q.ilike('comune',    `%${input.comune}%`)
    if (input.prezzo_min != null) q = q.gte('prezzo_base', input.prezzo_min)
    if (input.prezzo_max != null) q = q.lte('prezzo_base', input.prezzo_max)
    if (input.mq_min != null)     q = q.gte('mq', input.mq_min)
    if (input.mq_max != null)     q = q.lte('mq', input.mq_max)
    if (input.tipologia)   q = q.ilike('tipologia',   `%${input.tipologia}%`)
    if (input.occupazione) q = q.ilike('occupazione', `%${input.occupazione}%`)

    for (const f of ['abuso_edilizio','ipoteca_presente','occupato_terzi',
      'spese_condominiali_arretrate','difformita_catastale',
      'necessita_ristrutturazione','amianto_presente']) {
      if (input[f] != null) q = q.eq(f, input[f])
    }

    if (input.rischio_min != null) q = q.gte('punteggio_rischio', input.rischio_min)
    if (input.rischio_max != null) q = q.lte('punteggio_rischio', input.rischio_max)
    if (input.sconto_min  != null) q = q.gte('sconto_perizia_pct', input.sconto_min)
    if (input.problema)            q = q.contains('problemi_rilevati', [input.problema])
    if (input.data_da)             q = q.gte('data_pubblicazione', input.data_da)
    if (input.solo_con_ribassi)    q = q.gt('num_ribassi', 0)

    q = q.order(col, { ascending: !desc }).limit(limit)
    const { data, error } = await q
    return error ? { error: error.message } : { risultati: data ?? [], count: data?.length ?? 0 }
  }

  if (name === 'statistiche') {
    const gruppo = String(input.gruppo_per ?? 'provincia')
    const { data, error } = await supabase.from('v_aste_complete')
      .select(`${gruppo},prezzo_base,punteggio_rischio,abuso_edilizio,occupato_terzi,sconto_perizia_pct`)
    if (error) return { error: error.message }

    const g: Record<string, {
      count:number; prezzi:number[]; rischi:number[]; sconti:number[]; abusi:number; occupati:number
    }> = {}
    for (const _r of data ?? []) {
      const r = (_r as unknown) as Record<string, unknown>
      const k = r[gruppo] as string ?? 'N/D'
      if (!g[k]) g[k] = { count:0, prezzi:[], rischi:[], sconti:[], abusi:0, occupati:0 }
      g[k].count++
      if (r['prezzo_base'])        g[k].prezzi.push(r['prezzo_base'] as number)
      if (r['punteggio_rischio'])  g[k].rischi.push(r['punteggio_rischio'] as number)
      if (r['sconto_perizia_pct']) g[k].sconti.push(r['sconto_perizia_pct'] as number)
      if (r['abuso_edilizio'])     g[k].abusi++
      if (r['occupato_terzi'])     g[k].occupati++
    }
    const avg = (a: number[]) => a.length ? Math.round(a.reduce((s,v)=>s+v,0)/a.length) : null
    return {
      statistiche: Object.entries(g)
        .sort((a,b) => b[1].count - a[1].count).slice(0, 25)
        .map(([k, v]) => ({
          [gruppo]: k,
          totale_aste: v.count,
          prezzo_medio: avg(v.prezzi),
          sconto_medio_pct: v.sconti.length ? Math.round(avg(v.sconti)! * 10) / 10 : null,
          rischio_medio: v.rischi.length ? Math.round(avg(v.rischi)! * 10) / 10 : null,
          con_abuso: v.abusi,
          occupate: v.occupati,
        })),
      raggruppate_per: gruppo,
    }
  }

  if (name === 'dettaglio_asta') {
    const { data, error } = await supabase.from('v_aste_complete')
      .select('*').eq('pvp_id', input.pvp_id).single()
    if (error) return { error: `Asta ${input.pvp_id} non trovata` }
    const docs    = await supabase.from('documenti')
      .select('nome_file,tipo,url_originale,scaricato,num_pagine').eq('asta_id', data.id)
    const ribassi = await supabase.from('aste_prezzi_history')
      .select('prezzo_base_old,prezzo_base_new,variazione_pct,rilevato_il')
      .eq('pvp_id', input.pvp_id).order('rilevato_il', { ascending: false })
    return { ...data, documenti: docs.data ?? [], storico_prezzi: ribassi.data ?? [] }
  }

  if (name === 'storico_prezzi') {
    let q = supabase.from('aste_prezzi_history')
      .select('pvp_id,prezzo_base_old,prezzo_base_new,variazione_pct,rilevato_il')
      .order('rilevato_il', { ascending: false })
    if (input.pvp_id) q = q.eq('pvp_id', input.pvp_id)
    q = q.limit(Number(input.limit ?? 20))
    const { data, error } = await q
    return error ? { error: error.message } : { ribassi: data ?? [], count: data?.length ?? 0 }
  }

  return { error: `Tool sconosciuto: ${name}` }
}

const SYSTEM = `Sei un assistente esperto in aste giudiziarie immobiliari italiane.
Hai accesso a un database aggiornato quotidianamente con tutte le aste del PVP (Portale Vendite Pubbliche).

DATABASE:
- Aste con dati strutturati (prezzo, superficie, localizzazione, tipologia)
- Analisi AI: problemi rilevati, punteggio rischio 1-10, sconto su perizia
- Storico variazioni di prezzo (ribassi d'asta)

REGOLE:
- Usa i tool prima di rispondere
- Indica sempre quante aste hai trovato
- Per ogni asta mostra: comune, prezzo, sconto perizia (se disponibile), problemi principali
- Se 0 risultati, suggerisci filtri alternativi
- Rispondi in italiano, sii conciso e diretto`

// ── Handler ────────────────────────────────────────────────────────────
export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') return res.status(405).end()

  const { userMessage, sessionId } = req.body as {
    userMessage: string
    sessionId: string
  }

  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')

  const send = (data: object) => res.write(`data: ${JSON.stringify(data)}\n\n`)

  try {
    // #8 — La sessione nel DB è la fonte di verità della storia
    // Carichiamo la storia dal DB e aggiungiamo solo il nuovo messaggio utente
    let history: Anthropic.MessageParam[] = []
    let isFirstMessage = false

    if (sessionId) {
      const { data: session } = await supabase
        .from('chat_sessions').select('messages').eq('id', sessionId).single()
      if (session?.messages && Array.isArray(session.messages)) {
        history = session.messages as Anthropic.MessageParam[]
      }
      isFirstMessage = history.length === 0
    }

    // Aggiungi il nuovo messaggio utente alla storia
    history.push({ role: 'user', content: userMessage })

    // Agentic loop
    for (let i = 0; i < 5; i++) {
      const resp = await anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4000,
        system: SYSTEM,
        tools: TOOLS,
        messages: history,
      })

      // Aggiungi la risposta dell'assistente alla storia
      history.push({ role: 'assistant', content: resp.content })

      if (resp.stop_reason === 'end_turn') {
        for (const block of resp.content) {
          if (block.type === 'text') {
            send({ type: 'text', text: block.text })
          }
        }
        break
      }

      if (resp.stop_reason === 'tool_use') {
        const toolResults: Anthropic.ToolResultBlockParam[] = []
        for (const block of resp.content) {
          if (block.type === 'tool_use') {
            send({ type: 'tool_call', tool: block.name, input: block.input })
            const result = await executeTool(block.name, block.input as Record<string, unknown>)
            send({ type: 'tool_result', tool: block.name, result })
            toolResults.push({
              type: 'tool_result',
              tool_use_id: block.id,
              content: JSON.stringify(result),
            })
          }
        }
        history.push({ role: 'user', content: toolResults })
        continue
      }
      break
    }

    // #8 — Salva storia aggiornata nel DB
    // Il titolo viene impostato solo al primo messaggio e non viene più sovrascritto
    if (sessionId) {
      const updateData: Record<string, unknown> = { messages: history }
      if (isFirstMessage) {
        updateData.titolo = userMessage.slice(0, 60)
      }
      await supabase.from('chat_sessions').update(updateData).eq('id', sessionId)
      send({ type: 'session_saved' })
    }

  } catch (err) {
    send({ type: 'error', message: String(err) })
  }

  send({ type: 'done' })
  res.end()
}
