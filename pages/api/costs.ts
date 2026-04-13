/**
 * #10 — API costi: restituisce l'andamento delle spese Claude per run.
 *
 * GET /api/costs              → ultimi 30 run
 * GET /api/costs?days=7       → run degli ultimi 7 giorni
 * GET /api/costs?summary=true → sommario mensile
 */
import type { NextApiRequest, NextApiResponse } from 'next'
import { createClient } from '@supabase/supabase-js'

const sb = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
)

// Prezzo Claude Sonnet (€ per milione di token, tasso di cambio 0.93)
const PRICE_IN  = 3.0  * 0.93 / 1_000_000   // € per token input
const PRICE_OUT = 15.0 * 0.93 / 1_000_000   // € per token output

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'GET') return res.status(405).end()

  const days    = parseInt(String(req.query.days  ?? '30'))
  const summary = req.query.summary === 'true'

  const since = new Date()
  since.setDate(since.getDate() - days)

  const { data: runs, error } = await sb
    .from('scraping_runs')
    .select(
      'id,mode,status,started_at,ended_at,' +
      'aste_scraped,aste_nuove,aste_analizzate,aste_errori,' +
      'costo_stimato_eur,tokens_input_totali,tokens_output_totali'
    )
    .gte('started_at', since.toISOString())
    .eq('status', 'completed')
    .order('started_at', { ascending: false })
    .limit(100)

  if (error) return res.status(500).json({ error: error.message })

  const rows = runs ?? []

  if (summary) {
    // Aggregato mensile
    const totals = rows.reduce(
      (acc, r) => ({
        runs:           acc.runs + 1,
        aste_totali:    acc.aste_totali    + (r.aste_scraped     || 0),
        aste_analizzate:acc.aste_analizzate + (r.aste_analizzate || 0),
        costo_eur:      acc.costo_eur      + (r.costo_stimato_eur || 0),
        tokens_in:      acc.tokens_in      + (r.tokens_input_totali  || 0),
        tokens_out:     acc.tokens_out     + (r.tokens_output_totali || 0),
      }),
      { runs: 0, aste_totali: 0, aste_analizzate: 0, costo_eur: 0, tokens_in: 0, tokens_out: 0 }
    )
    return res.json({
      period_days:   days,
      ...totals,
      costo_eur:     Math.round(totals.costo_eur * 10000) / 10000,
      costo_medio_per_asta: totals.aste_analizzate
        ? Math.round(totals.costo_eur / totals.aste_analizzate * 10000) / 10000
        : 0,
      proiezione_mensile_eur: days > 0
        ? Math.round(totals.costo_eur / days * 30 * 100) / 100
        : 0,
    })
  }

  // Lista run
  return res.json(rows.map(r => ({
    ...r,
    durata_min: r.started_at && r.ended_at
      ? Math.round((new Date(r.ended_at).getTime() - new Date(r.started_at).getTime()) / 60000)
      : null,
  })))
}
