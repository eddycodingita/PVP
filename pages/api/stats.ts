import type { NextApiRequest, NextApiResponse } from 'next'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_KEY!
)

export default async function handler(_req: NextApiRequest, res: NextApiResponse) {
  try {
    const oggi = new Date().toISOString().split('T')[0]

    const [total, analizzate, nuove] = await Promise.all([
      supabase.from('aste').select('id', { count: 'exact', head: true }).eq('is_active', true),
      supabase.from('analisi_ai').select('id', { count: 'exact', head: true }),
      supabase.from('aste').select('id', { count: 'exact', head: true })
        .gte('data_pubblicazione', oggi),
    ])

    res.json({
      aste:       total.count?.toLocaleString('it-IT') ?? '—',
      analizzate: analizzate.count?.toLocaleString('it-IT') ?? '—',
      oggi:       nuove.count?.toString() ?? '—',
    })
  } catch {
    res.json({ aste: '—', analizzate: '—', oggi: '—' })
  }
}
