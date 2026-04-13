import { useState, useRef, useEffect, useCallback } from 'react'
import Head from 'next/head'

// ── Types ──────────────────────────────────────────────────────────────
type AstaResult = {
  pvp_id: string; url_dettaglio: string | null
  comune: string; provincia: string; tipologia: string
  prezzo_base: number | null; prezzo_precedente: number | null
  mq: number | null; occupazione: string | null
  data_pubblicazione: string | null; data_vendita: string | null
  abuso_edilizio: boolean | null; ipoteca_presente: boolean | null
  occupato_terzi: boolean | null; spese_condominiali_arretrate: boolean | null
  difformita_catastale: boolean | null; necessita_ristrutturazione: boolean | null
  amianto_presente: boolean | null; punteggio_rischio: number | null
  problemi_rilevati: string[] | null; descrizione_sintetica: string | null
  valore_perizia: number | null; sconto_perizia_pct: number | null
  num_ribassi: number | null; num_documenti: number | null
}
type StatRow  = Record<string, string | number | null>
type Session  = { id: string; titolo: string | null; aggiornata_il: string }
type CostSummary = {
  runs: number; aste_totali: number; aste_analizzate: number
  costo_eur: number; costo_medio_per_asta: number; proiezione_mensile_eur: number
}
type RunRow = {
  id: string; mode: string; started_at: string
  aste_analizzate: number; costo_stimato_eur: number; durata_min: number | null
}
type Message = {
  id: string; role: 'user' | 'assistant'; text: string
  aste?: AstaResult[]; stats?: StatRow[]; statsKey?: string
  toolCalls?: { tool: string; label: string }[]
  loading?: boolean
}
type AnthropicMsg = {
  role: 'user' | 'assistant'
  content: string | Array<{ type: string; text?: string; [k: string]: unknown }>
}

const QUICK = [
  { icon: '⚠',  q: 'Mostrami le aste con abusi edilizi' },
  { icon: '📉', q: 'Aste con il maggiore sconto sul valore di perizia' },
  { icon: '🏠', q: 'Aste libere con prezzo base sotto 80.000 euro' },
  { icon: '📊', q: 'Statistiche aggregate per regione' },
  { icon: '💰', q: 'Aste che hanno subito ribassi di prezzo di recente' },
  { icon: '⚡', q: 'Aste con punteggio rischio massimo 3' },
  { icon: '🔑', q: 'Aste occupate da terzi o con contratto di locazione' },
  { icon: '📍', q: 'Nuove aste pubblicate oggi' },
]

const PROBLEM_TAGS: Record<string, { label: string; cls: string }> = {
  abuso_edilizio:               { label: 'Abuso edilizio',   cls: 'danger'  },
  ipoteca_presente:             { label: 'Ipoteca',          cls: 'warning' },
  occupato_terzi:               { label: 'Occupata terzi',   cls: 'danger'  },
  spese_condominiali_arretrate: { label: 'Spese cond.',      cls: 'warning' },
  difformita_catastale:         { label: 'Difformità cat.',  cls: 'warning' },
  necessita_ristrutturazione:   { label: 'Da ristrutturare', cls: 'info'    },
  amianto_presente:             { label: 'Amianto',          cls: 'danger'  },
}

const fmt     = (n: number | null, dec = 0) =>
  n == null ? '—' : '€' + n.toLocaleString('it-IT', { minimumFractionDigits: dec, maximumFractionDigits: dec })
const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'
const timeAgo = (s: string) => {
  const d = Date.now() - new Date(s).getTime()
  if (d < 3_600_000)  return `${Math.floor(d / 60_000)}m fa`
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h fa`
  return new Date(s).toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })
}

// Estrae testo leggibile da un messaggio Anthropic (content può essere string o array)
function extractDisplayText(m: AnthropicMsg): string {
  if (typeof m.content === 'string') return m.content
  if (!Array.isArray(m.content))     return ''
  return m.content
    .filter((b): b is { type: 'text'; text: string } => b.type === 'text' && typeof b.text === 'string')
    .map(b => b.text)
    .join('\n')
    .trim()
}

// ── AstaCard ────────────────────────────────────────────────────────
function AstaCard({ asta }: { asta: AstaResult }) {
  const rischio = asta.punteggio_rischio ?? 0
  const tags    = Object.entries(PROBLEM_TAGS)
    .filter(([k]) => (asta as Record<string,unknown>)[k] === true)
    .map(([, v]) => v)

  return (
    <div className="asta-card"
      onClick={() => asta.url_dettaglio && window.open(asta.url_dettaglio, '_blank')}>
      <div className="asta-card-top">
        <div>
          <div className="asta-card-id">PVP#{asta.pvp_id}</div>
          <div className="asta-card-location">
            {asta.comune}{asta.provincia ? ` (${asta.provincia})` : ''}
            {asta.tipologia && (
              <span style={{ color: 'var(--text-3)', fontWeight: 400, fontSize: 13 }}>
                {' · '}{asta.tipologia}
              </span>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {rischio > 0 && <span className={`rischio-badge rischio-${rischio}`}>{rischio}</span>}
          <div>
            <div className="asta-card-price">{fmt(asta.prezzo_base)}</div>
            {asta.prezzo_precedente && (
              <div style={{ fontSize: 11, color: 'var(--green)',
                fontFamily: "'IBM Plex Mono',monospace", textAlign: 'right' }}>
                ↓ da {fmt(asta.prezzo_precedente)}
              </div>
            )}
          </div>
        </div>
      </div>

      {asta.sconto_perizia_pct != null && (
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12,
          color: 'var(--green)', fontFamily: "'IBM Plex Mono',monospace",
          background: 'rgba(63,182,138,0.1)', border: '1px solid rgba(63,182,138,0.25)',
          borderRadius: 4, padding: '2px 7px', marginBottom: 8 }}>
          📉 Sconto perizia: {asta.sconto_perizia_pct}%
        </div>
      )}

      {asta.descrizione_sintetica && (
        <div className="asta-card-desc">{asta.descrizione_sintetica}</div>
      )}

      <div style={{ display: 'flex', gap: 12, fontSize: 12, color: 'var(--text-3)',
        fontFamily: "'IBM Plex Mono',monospace", marginBottom: tags.length ? 8 : 0 }}>
        {asta.mq         ? <span>{asta.mq} mq</span>               : null}
        {asta.occupazione ? (
          <span style={{ color: asta.occupazione.toLowerCase().includes('libero')
            ? 'var(--green)' : 'var(--red)' }}>
            {asta.occupazione}
          </span>
        ) : null}
        {asta.data_vendita  ? <span>Vendita: {fmtDate(asta.data_vendita)}</span> : null}
        {asta.num_ribassi   ? <span style={{ color: 'var(--gold)' }}>↓{asta.num_ribassi} ribassi</span> : null}
        {asta.num_documenti ? <span>{asta.num_documenti} doc</span> : null}
      </div>

      {tags.length > 0 && (
        <div className="asta-card-tags">
          {tags.map((t, i) => <span key={i} className={`tag ${t.cls}`}>{t.label}</span>)}
        </div>
      )}
    </div>
  )
}

// ── StatsTable ──────────────────────────────────────────────────────
function StatsTable({ stats, groupKey }: { stats: StatRow[]; groupKey: string }) {
  if (!stats.length) return null
  const keys = Object.keys(stats[0])
  const labels: Record<string, string> = {
    totale_aste: 'Aste', prezzo_medio: 'Prezzo medio',
    sconto_medio_pct: 'Sconto %', rischio_medio: 'Rischio',
    con_abuso: 'Abusi', occupate: 'Occupate',
  }
  return (
    <table className="stats-table">
      <thead><tr>{keys.map(k => <th key={k}>{labels[k] ?? k}</th>)}</tr></thead>
      <tbody>
        {stats.map((r, i) => (
          <tr key={i}>
            {keys.map(k => {
              const v = r[k]; const isMoney = k.startsWith('prezzo')
              return (
                <td key={k} className={k !== groupKey ? 'mono' : ''}>
                  {v == null ? '—' : isMoney ? '€' + Number(v).toLocaleString('it-IT') : String(v)}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── #10 Cost Dashboard ──────────────────────────────────────────────
function CostDashboard({ onClose }: { onClose: () => void }) {
  const [summary, setSummary]   = useState<CostSummary | null>(null)
  const [runs, setRuns]         = useState<RunRow[]>([])
  const [loading, setLoading]   = useState(true)

  useEffect(() => {
    Promise.all([
      fetch('/api/costs?summary=true&days=30').then(r => r.json()),
      fetch('/api/costs?days=14').then(r => r.json()),
    ]).then(([s, r]) => {
      setSummary(s)
      setRuns(Array.isArray(r) ? r.slice(0, 10) : [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onClick={onClose}>
      <div style={{ background: 'var(--navy-2)', border: '1px solid var(--border)',
        borderRadius: 12, padding: 28, width: 520, maxHeight: '80vh',
        overflowY: 'auto', position: 'relative' }}
        onClick={e => e.stopPropagation()}>

        <div style={{ display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', marginBottom: 20 }}>
          <div>
            <div style={{ fontFamily: "'Playfair Display',serif", fontSize: 18,
              color: 'var(--gold)' }}>Costi Claude API</div>
            <div style={{ fontSize: 12, color: 'var(--text-3)',
              fontFamily: "'IBM Plex Mono',monospace" }}>ultimi 30 giorni</div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none',
            color: 'var(--text-2)', fontSize: 22, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>

        {loading ? (
          <div style={{ color: 'var(--text-3)', textAlign: 'center', padding: 40 }}>
            Caricamento…
          </div>
        ) : summary ? (
          <>
            {/* Sommario */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
              gap: 12, marginBottom: 24 }}>
              {[
                ['Spesa totale',      fmt(summary.costo_eur, 4),            'var(--gold)'],
                ['Per asta analizzata', fmt(summary.costo_medio_per_asta, 4), 'var(--text)'],
                ['Proiezione mensile', fmt(summary.proiezione_mensile_eur, 2), 'var(--green)'],
              ].map(([label, value, color]) => (
                <div key={label} style={{ background: 'var(--navy-3)',
                  border: '1px solid var(--border-2)', borderRadius: 8, padding: 14 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4,
                    fontFamily: "'IBM Plex Mono',monospace", letterSpacing: '0.05em' }}>
                    {label}
                  </div>
                  <div style={{ fontSize: 18, fontFamily: "'IBM Plex Mono',monospace",
                    color: color as string, fontWeight: 500 }}>
                    {value}
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 16, marginBottom: 20,
              fontSize: 13, color: 'var(--text-2)' }}>
              <span>🔄 {summary.runs} run completati</span>
              <span>🏠 {summary.aste_totali.toLocaleString('it-IT')} aste scrappate</span>
              <span>🤖 {summary.aste_analizzate.toLocaleString('it-IT')} analizzate</span>
            </div>

            {/* Ultimi run */}
            {runs.length > 0 && (
              <>
                <div style={{ fontSize: 11, color: 'var(--text-3)', letterSpacing: '0.1em',
                  fontFamily: "'IBM Plex Mono',monospace", textTransform: 'uppercase',
                  marginBottom: 10 }}>Ultimi run</div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border-2)' }}>
                      {['Data', 'Analizzate', 'Costo', 'Durata'].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '5px 8px',
                          color: 'var(--text-3)', fontWeight: 400,
                          fontFamily: "'IBM Plex Mono',monospace", fontSize: 11 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map(r => (
                      <tr key={r.id} style={{ borderBottom: '1px solid var(--border-2)' }}>
                        <td style={{ padding: '6px 8px', color: 'var(--text-2)' }}>
                          {new Date(r.started_at).toLocaleDateString('it-IT',
                            { day: '2-digit', month: 'short' })}
                        </td>
                        <td style={{ padding: '6px 8px', color: 'var(--text)',
                          fontFamily: "'IBM Plex Mono',monospace" }}>
                          {r.aste_analizzate || 0}
                        </td>
                        <td style={{ padding: '6px 8px', color: 'var(--gold)',
                          fontFamily: "'IBM Plex Mono',monospace" }}>
                          {r.costo_stimato_eur ? `€${r.costo_stimato_eur.toFixed(4)}` : '€0'}
                        </td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-3)',
                          fontFamily: "'IBM Plex Mono',monospace" }}>
                          {r.durata_min != null ? `${r.durata_min}m` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}

            <div style={{ marginTop: 16, padding: '10px 12px',
              background: 'rgba(201,168,76,0.06)', border: '1px solid var(--border)',
              borderRadius: 6, fontSize: 12, color: 'var(--text-3)' }}>
              💡 Prezzi Claude Sonnet: $3/M token input · $15/M token output (tasso €0.93)
            </div>
          </>
        ) : (
          <div style={{ color: 'var(--text-3)', textAlign: 'center', padding: 40 }}>
            Nessun dato disponibile. Avvia almeno un run della pipeline.
          </div>
        )}
      </div>
    </div>
  )
}

// ── SSE stream ──────────────────────────────────────────────────────
async function* streamChat(userMessage: string, sessionId: string) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userMessage, sessionId }),
  })
  const reader = res.body!.getReader()
  const dec    = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    const lines = buf.split('\n'); buf = lines.pop() ?? ''
    for (const ln of lines) {
      if (ln.startsWith('data: ')) {
        try { yield JSON.parse(ln.slice(6)) } catch { /* */ }
      }
    }
  }
}

// ── Main ────────────────────────────────────────────────────────────
export default function Home() {
  const [messages, setMessages]       = useState<Message[]>([])
  const [input, setInput]             = useState('')
  const [loading, setLoading]         = useState(false)
  const [sessions, setSessions]       = useState<Session[]>([])
  const [sessionId, setSessionId]     = useState<string | null>(null)
  const [dbStats, setDbStats]         = useState({ aste: '—', analizzate: '—', oggi: '—' })
  const [exporting, setExporting]     = useState(false)
  // #9 — Filtri dell'ultima ricerca (per export contestuale)
  const [lastFilters, setLastFilters] = useState<Record<string, unknown>>({})
  const [hasResults, setHasResults]   = useState(false)
  // #10 — Dashboard costi
  const [showCosts, setShowCosts]     = useState(false)

  const chatRef  = useRef<HTMLDivElement>(null)
  const textaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    fetch('/api/sessions').then(r => r.json()).then(setSessions).catch(() => {})
    fetch('/api/stats').then(r => r.json()).then(setDbStats).catch(() => {})
  }, [])

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // #8 — Carica sessione: estrae testo dai blocchi Anthropic
  const loadSession = async (id: string) => {
    const r = await fetch(`/api/sessions?id=${id}`)
    const s = await r.json()
    if (!s?.messages) return
    setSessionId(id)
    const msgs: Message[] = (s.messages as AnthropicMsg[])
      .map(m => {
        const text = extractDisplayText(m)
        if (!text && m.role === 'user') return null
        return { id: crypto.randomUUID(), role: m.role, text: text || '…' }
      })
      .filter((m): m is Message => m !== null)
    setMessages(msgs)
    setHasResults(false)
    setLastFilters({})
  }

  const newSession = async () => {
    const r = await fetch('/api/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ titolo: 'Nuova conversazione', messages: [] }),
    })
    const s = await r.json()
    if (!s.id) return
    setSessionId(s.id)
    setMessages([])
    setHasResults(false)
    setLastFilters({})
    setSessions(prev => [{
      id: s.id, titolo: 'Nuova conversazione',
      aggiornata_il: new Date().toISOString(),
    }, ...prev])
  }

  const deleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    await fetch(`/api/sessions?id=${id}`, { method: 'DELETE' })
    setSessions(prev => prev.filter(s => s.id !== id))
    if (sessionId === id) {
      setSessionId(null); setMessages([])
      setHasResults(false); setLastFilters({})
    }
  }

  // #9 — Export con i filtri dell'ultima ricerca
  const exportCSV = async () => {
    setExporting(true)
    try {
      const r = await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: lastFilters }),
      })
      const blob = await r.blob()
      const url  = URL.createObjectURL(blob)
      // Estrai il filename dall'header Content-Disposition
      const cd   = r.headers.get('content-disposition') ?? ''
      const filename = cd.match(/filename="([^"]+)"/)?.[1] ?? 'aste_pvp.csv'
      Object.assign(document.createElement('a'), { href: url, download: filename }).click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  const send = useCallback(async (question: string) => {
    if (!question.trim() || loading) return
    setInput('')
    setLoading(true)

    let sid = sessionId
    if (!sid) {
      const r = await fetch('/api/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ titolo: question.slice(0, 60), messages: [] }),
      })
      const s = await r.json()
      sid = s.id ?? null
      setSessionId(sid)
      if (sid) {
        setSessions(prev => [{
          id: sid!, titolo: question.slice(0, 60),
          aggiornata_il: new Date().toISOString(),
        }, ...prev])
      }
    }

    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text: question }
    const aiMsg:   Message = { id: crypto.randomUUID(), role: 'assistant', text: '', loading: true, toolCalls: [] }
    setMessages(prev => [...prev, userMsg, aiMsg])

    let finalText = ''; let aste: AstaResult[] = []
    let stats: StatRow[] = []; let statsKey = ''
    const toolCalls: { tool: string; label: string }[] = []

    try {
      for await (const ev of streamChat(question, sid ?? '')) {
        if (ev.type === 'tool_call') {
          const labels: Record<string, string> = {
            cerca_aste: 'Ricerca nel database...', statistiche: 'Calcolo statistiche...',
            dettaglio_asta: 'Recupero dettaglio...', storico_prezzi: 'Carico storico prezzi...',
          }
          toolCalls.push({ tool: ev.tool, label: labels[ev.tool] ?? ev.tool })
          setMessages(prev => prev.map(m =>
            m.id === aiMsg.id ? { ...m, toolCalls: [...toolCalls] } : m
          ))
          // #9 — Cattura i filtri dell'ultima cerca_aste
          if (ev.tool === 'cerca_aste') {
            setLastFilters(ev.input as Record<string, unknown>)
          }
        }

        if (ev.type === 'tool_result') {
          if (ev.tool === 'cerca_aste' && ev.result?.risultati) {
            aste = ev.result.risultati
            setHasResults(aste.length > 0)
          }
          if (ev.tool === 'statistiche' && ev.result?.statistiche) {
            stats    = ev.result.statistiche
            statsKey = ev.result.raggruppate_per ?? 'gruppo'
          }
        }

        if (ev.type === 'text') {
          finalText = ev.text
          setMessages(prev => prev.map(m =>
            m.id === aiMsg.id ? { ...m, text: finalText, loading: false } : m
          ))
        }

        if (ev.type === 'done') {
          setMessages(prev => prev.map(m =>
            m.id === aiMsg.id
              ? { ...m, text: finalText, aste, stats, statsKey, toolCalls, loading: false }
              : m
          ))
          setSessions(prev => prev.map(s =>
            s.id === sid
              ? { ...s, aggiornata_il: new Date().toISOString() }
              : s
          ))
        }
      }
    } catch {
      setMessages(prev => prev.map(m =>
        m.id === aiMsg.id
          ? { ...m, text: 'Errore di connessione. Riprova.', loading: false }
          : m
      ))
    }
    setLoading(false)
  }, [loading, sessionId])

  return (
    <>
      <Head>
        <title>PVP Monitor — Aste Immobiliari</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      {/* #10 — Cost dashboard modal */}
      {showCosts && <CostDashboard onClose={() => setShowCosts(false)} />}

      <div className="app">
        {/* ── Sidebar ── */}
        <aside className="sidebar">
          <div className="sidebar-logo">
            <h1>PVP Monitor</h1>
            <p>portale vendite pubbliche</p>
          </div>

          <div style={{ padding: '12px 20px 0' }}>
            <button onClick={newSession} style={{
              width: '100%', background: 'rgba(201,168,76,0.1)',
              border: '1px solid rgba(201,168,76,0.3)', borderRadius: 6,
              color: 'var(--gold)', padding: '8px 12px', cursor: 'pointer',
              fontSize: 13, fontFamily: "'DM Sans',sans-serif", transition: 'all .15s',
            }}>
              + Nuova conversazione
            </button>
          </div>

          {sessions.length > 0 && (
            <div className="sidebar-section">
              <div className="sidebar-section-label">Conversazioni</div>
              {sessions.map(s => (
                <div key={s.id} onClick={() => loadSession(s.id)} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '7px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 2,
                  background: sessionId === s.id ? 'var(--navy-3)' : 'transparent',
                  border: sessionId === s.id ? '1px solid var(--border)' : '1px solid transparent',
                  transition: 'all .15s',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, color: 'var(--text)', overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s.titolo || 'Conversazione'}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-3)',
                      fontFamily: "'IBM Plex Mono',monospace" }}>
                      {timeAgo(s.aggiornata_il)}
                    </div>
                  </div>
                  <button onClick={e => deleteSession(s.id, e)} style={{
                    background: 'none', border: 'none', color: 'var(--text-3)',
                    cursor: 'pointer', padding: '2px 6px', fontSize: 16, lineHeight: 1,
                  }}>×</button>
                </div>
              ))}
            </div>
          )}

          <div className="sidebar-section">
            <div className="sidebar-section-label">Query rapide</div>
            {QUICK.map((q, i) => (
              <button key={i} className="quick-btn" onClick={() => send(q.q)} disabled={loading}>
                <span style={{ marginRight: 8, fontSize: 14 }}>{q.icon}</span>
                {q.q.length > 34 ? q.q.slice(0, 34) + '…' : q.q}
              </button>
            ))}
          </div>

          <div className="sidebar-stats">
            <div className="sidebar-section-label" style={{ marginBottom: 12 }}>Database</div>
            {[['Aste totali', dbStats.aste], ['Analizzate AI', dbStats.analizzate], ['Oggi', dbStats.oggi]].map(([l, v]) => (
              <div key={l} className="stat-row">
                <span className="stat-label">{l}</span>
                <span className="stat-value">{v}</span>
              </div>
            ))}
            {/* #10 — Link al dashboard costi */}
            <button onClick={() => setShowCosts(true)} style={{
              marginTop: 12, width: '100%', background: 'transparent',
              border: '1px solid var(--border-2)', borderRadius: 5,
              color: 'var(--text-3)', fontSize: 11, padding: '6px 8px',
              cursor: 'pointer', fontFamily: "'IBM Plex Mono',monospace",
              transition: 'all .15s', letterSpacing: '0.05em',
            }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--gold-dim)'; e.currentTarget.style.color = 'var(--gold)' }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-2)'; e.currentTarget.style.color = 'var(--text-3)' }}
            >
              € Costi API
            </button>
          </div>
        </aside>

        {/* ── Main ── */}
        <main className="main">
          <div className="header">
            <span className="header-title">
              {sessionId ? 'conversazione attiva' : 'assistente · aste giudiziarie'}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {/* #9 — Export CSV filtrato */}
              {hasResults && (
                <button onClick={exportCSV} disabled={exporting} style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  background: 'transparent', border: '1px solid var(--border)',
                  borderRadius: 6, color: 'var(--text-2)', fontSize: 12,
                  fontFamily: "'IBM Plex Mono',monospace", padding: '5px 10px',
                  cursor: exporting ? 'wait' : 'pointer', transition: 'all .15s',
                }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--gold-dim)'; e.currentTarget.style.color = 'var(--gold)' }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-2)' }}
                >
                  {exporting
                    ? <span style={{ fontFamily: "'IBM Plex Mono',monospace" }}>…</span>
                    : <>↓ CSV</>
                  }
                </button>
              )}
              <span className="header-badge">live · aggiornato oggi</span>
            </div>
          </div>

          <div className="chat-area" ref={chatRef}>
            {messages.length === 0 ? (
              <div className="chat-empty">
                <div className="chat-empty-icon">
                  <svg width="24" height="24" fill="none" stroke="var(--gold)"
                    strokeWidth="1.5" viewBox="0 0 24 24">
                    <path d="M3 21l1.65-3.8a9 9 0 1 1 3.4 2.9L3 21" />
                  </svg>
                </div>
                <h2>Cosa vuoi sapere?</h2>
                <p>
                  Interroga il database delle aste in linguaggio naturale.
                  Chiedi di problemi, aree geografiche, prezzi o sconti su perizia.
                </p>
              </div>
            ) : messages.map(msg => (
              <div key={msg.id} className="msg">
                <div className={`msg-avatar ${msg.role}`}>
                  {msg.role === 'user' ? 'U' : 'AI'}
                </div>
                <div className="msg-body">
                  <div className="msg-name">
                    {msg.role === 'user' ? 'Tu' : 'Assistente'}
                  </div>

                  {msg.toolCalls && msg.toolCalls.length > 0 && (
                    <div style={{ marginBottom: 8, display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {msg.toolCalls.map((tc, i) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6,
                          fontSize: 12, color: 'var(--text-3)',
                          fontFamily: "'IBM Plex Mono',monospace" }}>
                          <svg width="12" height="12" fill="none" stroke="var(--gold-dim)"
                            strokeWidth="1.5" viewBox="0 0 24 24">
                            <circle cx="11" cy="11" r="8" />
                            <path d="m21 21-4.35-4.35" />
                          </svg>
                          {tc.label}
                        </div>
                      ))}
                    </div>
                  )}

                  {msg.loading && (
                    <div className="thinking">
                      <div className="thinking-dots"><span /><span /><span /></div>
                      <span>elaborazione</span>
                    </div>
                  )}

                  {msg.text && <div className={`msg-text ${msg.role}`}>{msg.text}</div>}

                  {msg.aste && msg.aste.length > 0 && (
                    <div className="results-grid">
                      {msg.aste.map(a => <AstaCard key={a.pvp_id} asta={a} />)}
                    </div>
                  )}

                  {msg.stats && msg.stats.length > 0 && (
                    <StatsTable stats={msg.stats} groupKey={msg.statsKey ?? 'provincia'} />
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="input-area">
            <div className="input-wrap">
              <textarea
                ref={textaRef}
                value={input}
                onChange={e => {
                  setInput(e.target.value)
                  e.target.style.height = 'auto'
                  e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px'
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
                }}
                placeholder="Scrivi la tua domanda sulle aste..."
                rows={1}
                disabled={loading}
              />
              <button className="send-btn" onClick={() => send(input)}
                disabled={loading || !input.trim()}>
                <svg width="16" height="16" fill="none" stroke="currentColor"
                  strokeWidth="2" viewBox="0 0 24 24">
                  <path d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </button>
            </div>
            <div className="input-hint">Invio per inviare · Shift+Invio per andare a capo</div>
          </div>
        </main>
      </div>
    </>
  )
}
