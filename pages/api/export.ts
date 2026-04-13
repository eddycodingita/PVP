/**
 * #9 — Export CSV delle aste.
 *
 * Accetta i filtri usati nell'ultima ricerca chat (passati dal frontend)
 * così il CSV rispecchia esattamente i risultati mostrati, non tutto il DB.
 *
 * POST /api/export
 * Body: { filters: { provincia?, prezzo_max?, ... }, format?: 'csv' | 'excel' }
 */
import type { NextApiRequest, NextApiResponse } from 'next'
import { createClient } from '@supabase/supabase-js'

const sb = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
)

// Colonne esportate con label italiana per l'intestazione
const EXPORT_COLUMNS: { key: string; label: string }[] = [
  { key: 'pvp_id',                       label: 'ID PVP' },
  { key: 'tribunale',                    label: 'Tribunale' },
  { key: 'numero_procedura',             label: 'N. Procedura' },
  { key: 'comune',                       label: 'Comune' },
  { key: 'provincia',                    label: 'Provincia' },
  { key: 'regione',                      label: 'Regione' },
  { key: 'indirizzo',                    label: 'Indirizzo' },
  { key: 'tipologia',                    label: 'Tipologia' },
  { key: 'prezzo_base',                  label: 'Prezzo base (€)' },
  { key: 'prezzo_precedente',            label: 'Prezzo precedente (€)' },
  { key: 'offerta_minima',               label: 'Offerta minima (€)' },
  { key: 'valore_perizia',               label: 'Valore perizia (€)' },
  { key: 'sconto_perizia_pct',           label: 'Sconto su perizia (%)' },
  { key: 'mq',                           label: 'Superficie (mq)' },
  { key: 'vani',                         label: 'Vani' },
  { key: 'piano',                        label: 'Piano' },
  { key: 'nr_locali',                    label: 'N. Locali' },
  { key: 'nr_bagni',                     label: 'Bagni' },
  { key: 'occupazione',                  label: 'Stato occupazione' },
  { key: 'data_pubblicazione',           label: 'Data pubblicazione' },
  { key: 'data_vendita',                 label: 'Data vendita' },
  { key: 'data_scadenza',                label: 'Scadenza offerte' },
  { key: 'punteggio_rischio',            label: 'Rischio AI (1-10)' },
  { key: 'abuso_edilizio',               label: 'Abuso edilizio' },
  { key: 'ipoteca_presente',             label: 'Ipoteca' },
  { key: 'ipoteca_importo',              label: 'Importo ipoteca (€)' },
  { key: 'occupato_terzi',               label: 'Occupato da terzi' },
  { key: 'contratto_locazione',          label: 'Contratto locazione' },
  { key: 'spese_condominiali_arretrate', label: 'Spese cond. arretrate' },
  { key: 'spese_condominiali_importo',   label: 'Importo spese cond. (€)' },
  { key: 'difformita_catastale',         label: 'Difformità catastale' },
  { key: 'necessita_ristrutturazione',   label: 'Necessita ristrutturazione' },
  { key: 'livello_ristrutturazione',     label: 'Livello ristrutturazione' },
  { key: 'amianto_presente',             label: 'Amianto' },
  { key: 'stato_conservazione',          label: 'Stato conservazione' },
  { key: 'classe_energetica',            label: 'Classe energetica' },
  { key: 'anno_costruzione',             label: 'Anno costruzione' },
  { key: 'problemi_rilevati',            label: 'Problemi rilevati' },
  { key: 'descrizione_sintetica',        label: 'Descrizione AI' },
  { key: 'num_ribassi',                  label: 'N. ribassi' },
  { key: 'num_documenti',               label: 'N. documenti' },
  { key: 'custode',                      label: 'Custode' },
  { key: 'custode_email',               label: 'Email custode' },
  { key: 'custode_tel',                  label: 'Tel. custode' },
  { key: 'delegato',                     label: 'Delegato' },
  { key: 'giudice',                      label: 'Giudice' },
  { key: 'url_dettaglio',               label: 'Link PVP' },
]

const KEYS = EXPORT_COLUMNS.map(c => c.key)

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') return res.status(405).end()

  // I filtri vengono passati dal frontend — corrispondono all'ultima cerca_aste
  const filters: Record<string, unknown> = req.body?.filters ?? {}
  const MAX_ROWS = 5000

  let q = sb.from('v_aste_complete').select(KEYS.join(','))

  // Applica gli stessi filtri usati nella chat
  if (filters.provincia)   q = q.ilike('provincia', `%${filters.provincia}%`)
  if (filters.regione)     q = q.ilike('regione',   `%${filters.regione}%`)
  if (filters.comune)      q = q.ilike('comune',    `%${filters.comune}%`)
  if (filters.tipologia)   q = q.ilike('tipologia', `%${filters.tipologia}%`)
  if (filters.occupazione) q = q.ilike('occupazione', `%${filters.occupazione}%`)

  if (filters.prezzo_min != null) q = q.gte('prezzo_base', filters.prezzo_min)
  if (filters.prezzo_max != null) q = q.lte('prezzo_base', filters.prezzo_max)
  if (filters.mq_min     != null) q = q.gte('mq', filters.mq_min)
  if (filters.mq_max     != null) q = q.lte('mq', filters.mq_max)

  for (const f of [
    'abuso_edilizio', 'ipoteca_presente', 'occupato_terzi',
    'spese_condominiali_arretrate', 'difformita_catastale',
    'necessita_ristrutturazione', 'amianto_presente',
  ]) {
    if (filters[f] != null) q = q.eq(f, filters[f])
  }

  if (filters.rischio_min != null) q = q.gte('punteggio_rischio', filters.rischio_min)
  if (filters.rischio_max != null) q = q.lte('punteggio_rischio', filters.rischio_max)
  if (filters.sconto_min  != null) q = q.gte('sconto_perizia_pct', filters.sconto_min)
  if (filters.problema)            q = q.contains('problemi_rilevati', [filters.problema])
  if (filters.data_da)             q = q.gte('data_pubblicazione', filters.data_da)
  if (filters.solo_con_ribassi)    q = q.gt('num_ribassi', 0)

  q = q.order('data_pubblicazione', { ascending: false }).limit(MAX_ROWS)

  const { data, error } = await q
  if (error) return res.status(500).json({ error: error.message })

  const rows = data ?? []
  const csv  = _buildCSV(rows)
  const date = new Date().toISOString().slice(0, 10)
  const filtroDesc = filters.comune || filters.provincia || filters.regione || 'italia'

  res.setHeader('Content-Type', 'text/csv; charset=utf-8')
  res.setHeader('Content-Disposition',
    `attachment; filename="aste_pvp_${filtroDesc}_${date}.csv"`)
  // BOM UTF-8 per apertura corretta in Excel italiano
  res.send('\uFEFF' + csv)
}

function _buildCSV(rows: Record<string, unknown>[]): string {
  const header = EXPORT_COLUMNS.map(c => _escape(c.label)).join(',')

  const body = rows.map(row =>
    KEYS.map(key => {
      const v = row[key]
      // Booleani → Sì/No per Excel italiano
      if (v === true)  return 'Sì'
      if (v === false) return 'No'
      // Array (problemi_rilevati) → stringa separata da punto e virgola
      if (Array.isArray(v)) return _escape(v.join('; '))
      // Date → formato italiano
      if (typeof v === 'string' && v.match(/^\d{4}-\d{2}-\d{2}/)) {
        return _formatDate(v)
      }
      return _escape(v)
    }).join(',')
  ).join('\n')

  return header + '\n' + body
}

function _escape(v: unknown): string {
  if (v == null) return ''
  const s = String(v)
  if (s.includes('"') || s.includes(',') || s.includes('\n') || s.includes(';'))
    return `"${s.replace(/"/g, '""')}"`
  return s
}

function _formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}/${d.getFullYear()}`
  } catch {
    return iso.slice(0, 10)
  }
}
