-- ============================================================
-- Migrazione 002 — Miglioramenti funzionali
-- Eseguire dopo 001_schema.sql nell'SQL Editor di Supabase
-- ============================================================

-- ── #5: Storico variazioni di prezzo ──────────────────────
CREATE TABLE IF NOT EXISTS aste_prezzi_history (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asta_id         UUID NOT NULL REFERENCES aste(id) ON DELETE CASCADE,
  pvp_id          TEXT NOT NULL,
  prezzo_base_old NUMERIC(14,2),
  prezzo_base_new NUMERIC(14,2),
  variazione_pct  NUMERIC(6,2),         -- negativo = ribasso
  rilevato_il     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_storia_asta ON aste_prezzi_history(asta_id);
CREATE INDEX IF NOT EXISTS idx_storia_data ON aste_prezzi_history(rilevato_il DESC);

-- ── #6: Valore perizia e sconto ───────────────────────────
ALTER TABLE analisi_ai
  ADD COLUMN IF NOT EXISTS valore_perizia     NUMERIC(14,2),
  ADD COLUMN IF NOT EXISTS sconto_perizia_pct NUMERIC(6,2); -- (perizia-prezzo)/perizia*100

-- ── #7: Alert rules ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_rules (
  id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  nome             TEXT NOT NULL,
  attivo           BOOLEAN DEFAULT TRUE,
  provincia        TEXT,
  regione          TEXT,
  comune           TEXT,
  prezzo_max       NUMERIC(14,2),
  prezzo_min       NUMERIC(14,2),
  mq_min           NUMERIC(10,2),
  tipologia        TEXT,
  solo_libere      BOOLEAN DEFAULT FALSE,
  rischio_max      INTEGER,
  problemi_esclusi TEXT[],
  canale           TEXT NOT NULL DEFAULT 'email',  -- email | telegram
  destinatario     TEXT NOT NULL,
  ultima_esecuzione TIMESTAMPTZ,
  creato_il        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alert_log (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  rule_id    UUID REFERENCES alert_rules(id) ON DELETE CASCADE,
  asta_id    UUID REFERENCES aste(id) ON DELETE CASCADE,
  pvp_id     TEXT,
  inviato_il TIMESTAMPTZ DEFAULT NOW(),
  successo   BOOLEAN DEFAULT TRUE,
  errore     TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_rule ON alert_log(rule_id);
CREATE INDEX IF NOT EXISTS idx_alert_asta ON alert_log(asta_id);

-- ── #8: Sessioni chat ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  titolo        TEXT,
  messages      JSONB NOT NULL DEFAULT '[]',
  creata_il     TIMESTAMPTZ DEFAULT NOW(),
  aggiornata_il TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_ts ON chat_sessions(aggiornata_il DESC);

CREATE OR REPLACE FUNCTION _touch_session()
RETURNS TRIGGER AS $$ BEGIN NEW.aggiornata_il = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_session_updated
BEFORE UPDATE ON chat_sessions FOR EACH ROW EXECUTE FUNCTION _touch_session();

-- ── #10: Costi per run ────────────────────────────────────
ALTER TABLE scraping_runs
  ADD COLUMN IF NOT EXISTS costo_stimato_eur    NUMERIC(8,4) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tokens_input_totali  INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tokens_output_totali INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS aste_analizzate      INTEGER DEFAULT 0;

-- ── #12: Materialized view (sostituisce la view normale) ──
DROP VIEW IF EXISTS v_aste_complete;

CREATE MATERIALIZED VIEW IF NOT EXISTS v_aste_complete AS
SELECT
  a.id, a.pvp_id, a.url_dettaglio,
  a.tribunale, a.numero_procedura, a.lotto,
  a.tipo_asta, a.tipologia, a.titolo,
  a.indirizzo, a.comune, a.provincia, a.regione, a.cap,
  a.latitudine, a.longitudine,
  a.mq, a.vani, a.piano, a.nr_locali, a.nr_bagni, a.nr_posti_auto,
  a.occupazione, a.prezzo_base, a.offerta_minima, a.rialzo_minimo,
  a.tipo_vendita, a.modalita_vendita,
  a.data_vendita, a.data_pubblicazione, a.data_scadenza,
  a.giudice, a.delegato, a.custode, a.custode_email, a.custode_tel,
  a.descrizione, a.scraped_at,
  ai.abuso_edilizio,          ai.abuso_edilizio_note,
  ai.sanatoria_pendente,      ai.vincoli_urbanistici,    ai.vincoli_note,
  ai.difformita_catastale,    ai.difformita_note,
  ai.ipoteca_presente,        ai.ipoteca_importo,
  ai.spese_condominiali_arretrate, ai.spese_condominiali_importo,
  ai.occupato_terzi,          ai.occupazione_note,
  ai.contratto_locazione,     ai.contratto_scadenza,
  ai.necessita_ristrutturazione, ai.livello_ristrutturazione,
  ai.amianto_presente,        ai.danni_strutturali,
  ai.anno_costruzione,        ai.stato_conservazione,
  ai.classe_energetica,       ai.impianti_conformi,    ai.certificato_agibilita,
  ai.descrizione_sintetica,   ai.problemi_rilevati,
  ai.punteggio_rischio,       ai.note_aggiuntive,
  ai.valore_perizia,          ai.sconto_perizia_pct,
  (SELECT COUNT(*) FROM documenti d WHERE d.asta_id = a.id)                      AS num_documenti,
  (SELECT COUNT(*) FROM documenti d WHERE d.asta_id = a.id AND d.scaricato)      AS num_doc_scaricati,
  (SELECT COUNT(*) FROM aste_prezzi_history h WHERE h.asta_id = a.id)            AS num_ribassi,
  (SELECT h.prezzo_base_old FROM aste_prezzi_history h
   WHERE h.asta_id = a.id ORDER BY h.rilevato_il DESC LIMIT 1)                  AS prezzo_precedente
FROM aste a
LEFT JOIN analisi_ai ai ON ai.asta_id = a.id
WHERE a.is_active = TRUE
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_pvp_id   ON v_aste_complete(pvp_id);
CREATE INDEX        IF NOT EXISTS idx_mv_provincia ON v_aste_complete(provincia);
CREATE INDEX        IF NOT EXISTS idx_mv_prezzo    ON v_aste_complete(prezzo_base);
CREATE INDEX        IF NOT EXISTS idx_mv_rischio   ON v_aste_complete(punteggio_rischio);
CREATE INDEX        IF NOT EXISTS idx_mv_data      ON v_aste_complete(data_pubblicazione DESC);
CREATE INDEX        IF NOT EXISTS idx_mv_problemi  ON v_aste_complete USING GIN(problemi_rilevati);

-- Funzione di refresh chiamata dalla pipeline giornaliera
CREATE OR REPLACE FUNCTION refresh_mv_aste()
RETURNS void LANGUAGE sql AS $$
  REFRESH MATERIALIZED VIEW CONCURRENTLY v_aste_complete;
$$;

-- ── #12: pg_cron — refresh automatico ogni 30 minuti ─────────────────
-- Richiede l'estensione pg_cron abilitata su Supabase
-- (Dashboard → Extensions → cerca "pg_cron" → Enable)
--
-- Dopo aver abilitato pg_cron, eseguire:

CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Refresh ogni 30 minuti (alle :00 e :30 di ogni ora)
SELECT cron.schedule(
  'refresh-mv-aste',           -- nome job (univoco)
  '*/30 * * * *',              -- cron expression
  $$SELECT refresh_mv_aste()$$ -- funzione da chiamare
);

-- Per vedere i job schedulati:
-- SELECT * FROM cron.job;

-- Per rimuovere il job:
-- SELECT cron.unschedule('refresh-mv-aste');

-- Per vedere i log di esecuzione:
-- SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20;
