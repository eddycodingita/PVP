import type { NextApiRequest, NextApiResponse } from 'next'
import { createClient } from '@supabase/supabase-js'

const sb = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
)

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const { method, query, body } = req

  // GET /api/sessions — lista sessioni
  if (method === 'GET' && !query.id) {
    const { data } = await sb.from('chat_sessions')
      .select('id,titolo,aggiornata_il')
      .order('aggiornata_il', { ascending: false })
      .limit(40)
    return res.json(data ?? [])
  }

  // GET /api/sessions?id=xxx — sessione completa
  if (method === 'GET' && query.id) {
    const { data } = await sb.from('chat_sessions')
      .select('*').eq('id', query.id).single()
    return data ? res.json(data) : res.status(404).json({ error: 'not found' })
  }

  // POST /api/sessions — crea nuova sessione
  if (method === 'POST') {
    const { titolo, messages } = body
    const { data } = await sb.from('chat_sessions')
      .insert({ titolo: titolo || 'Nuova conversazione', messages: messages || [] })
      .select('id').single()
    return res.json(data)
  }

  // DELETE /api/sessions?id=xxx — elimina sessione
  if (method === 'DELETE' && query.id) {
    await sb.from('chat_sessions').delete().eq('id', query.id)
    return res.json({ ok: true })
  }

  res.status(405).end()
}
