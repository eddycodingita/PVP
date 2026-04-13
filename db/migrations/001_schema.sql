-- ============================================================
-- PVP Monitor — Schema completo Supabase
-- Esegui tutto questo nell'SQL Editor di Supabase
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- TABELLA: aste
-- ============================================================
CREATE TABLE IF NOT EXISTS aste (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  pvp_id              TEXT UNIQUE NOT NULL,
  url_dettaglio       TEXT,
  titolo              TEXT,
  descrizione         TEXT,

  -- Procedura
  tribunale           TEXT,
  numero_procedura    TEXT,
  lotto               TEXT,
  tipo_asta           TEXT,
  tipologia           TEXT,

  -- Localizzazione
  indirizzo           TEXT,
  comune              TEXT,
  provincia           TEXT,
  regione             TEXT,
  cap                 TEXT,
  latitudine          DOUBLE PRECISION,
  longitudine         DOUBLE PRECISION,

  -- Immobile
  mq                  NUMERIC(10,2),
  vani                NUMERIC(5,1),
  piano               TEXT,
  nr_locali           INTEGER,
  nr_bagni            INTEGER,
  nr_posti_auto       INTEGER,
  foglio              TEXT,
  particella          TEXT,
  subalterno          TEXT,
  occupazione         TEXT,

  -- Prezzi
  prezzo_base         NUMERIC(14,2),
  offerta_minima      NUMERIC(14,2),
  rialzo_minimo       NUMERIC(14,2),

  -- Vendita
  tipo_vendita        TEXT,
  modalita_vendita    TEXT,
  data_vendita        TIMESTAMPTZ,
  data_pubblicazione  TIMESTAMPTZ,
  data_scadenza       TIMESTAMPTZ,

  -- Soggetti
  giudice             TEXT,
  delegato            TEXT,
  custode             TEXT,
  custode_email       TEXT,
  custode_tel         TEXT,

  -- Meta
  is_active           BOOLEAN DEFAULT TRUE,
  scraped_at          TIMESTAMPTZ DEFAULT NOW(),
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aste_pvp_id    ON aste(pvp_id);
CREATE INDEX IF NOT EXISTS idx_aste_provincia  ON aste(provincia);
CREATE INDEX IF NOT EXISTS idx_aste_comune     ON aste(comune);
CREATE INDEX IF NOT EXISTS idx_aste_tribunale  ON aste(tribunale);
CREATE INDEX IF NOT EXISTS idx_aste_data_pub   ON aste(data_pubblicazione DESC);
CREATE INDEX IF NOT EXISTS idx_aste_prezzo     ON aste(prezzo_base);
CREATE INDEX IF NOT EXISTS idx_aste_tipologia  ON aste(tipologia);
CREATE INDEX IF NOT EXISTS idx_aste_active     ON aste(is_active);
CREATE INDEX IF NOT EXISTS idx_aste_geo        ON aste(latitudine, longitudine);

-- Auto-aggiorna updated_at
CREATE OR REPLACE FUNCTION _set_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_aste_updated
BEFORE UPDATE ON aste FOR EACH ROW EXECUTE FUNCTION _set_updated_at();


-- ============================================================
-- TABELLA: documenti
-- ============================================================
CREATE TABLE IF NOT EXISTS documenti (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asta_id         UUID NOT NULL REFERENCES aste(id) ON DELETE CASCADE,
  nome_file       TEXT,
  tipo            TEXT,           -- perizia | avviso_vendita | planimetria | allegato
  url_originale   TEXT NOT NULL,
  storage_path    TEXT,           -- path in Supabase Storage bucket
  testo_estratto  TEXT,           -- testo raw estratto dal PDF
  num_pagine      INTEGER,
  dimensione_kb   INTEGER,
  scaricato       BOOLEAN DEFAULT FALSE,
  analizzato      BOOLEAN DEFAULT FALSE,
  errore          TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(asta_id, url_originale)
);

CREATE INDEX IF NOT EXISTS idx_doc_asta_id    ON documenti(asta_id);
CREATE INDEX IF NOT EXISTS idx_doc_tipo       ON documenti(tipo);
CREATE INDEX IF NOT EXISTS idx_doc_scaricato  ON documenti(scaricato);
CREATE INDEX IF NOT EXISTS idx_doc_analizzato ON documenti(analizzato);


-- ============================================================
-- TABELLA: analisi_ai
-- Risultato strutturato dell'analisi Claude sui documenti
-- ============================================================
CREATE TABLE IF NOT EXISTS analisi_ai (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asta_id         UUID NOT NULL REFERENCES aste(id) ON DELETE CASCADE UNIQUE,

  -- Problemi edilizi / urbanistici
  abuso_edilizio          BOOLEAN,
  abuso_edilizio_note     TEXT,
  sanatoria_pendente      BOOLEAN,
  vincoli_urbanistici     BOOLEAN,
  vincoli_note            TEXT,
  difformita_catastale    BOOLEAN,
  difformita_note         TEXT,

  -- Problemi economici
  ipoteca_presente        BOOLEAN,
  ipoteca_importo         NUMERIC(14,2),
  ipoteca_note            TEXT,
  spese_condominiali_arretrate  BOOLEAN,
  spese_condominiali_importo    NUMERIC(14,2),
  debiti_fiscali          BOOLEAN,
  debiti_note             TEXT,

  -- Occupazione
  occupato_terzi          BOOLEAN,
  contratto_locazione     BOOLEAN,
  contratto_scadenza      TEXT,
  occupazione_note        TEXT,

  -- Stato fisico
  necessita_ristrutturazione  BOOLEAN,
  livello_ristrutturazione    TEXT,   -- totale | parziale | minima
  amianto_presente        BOOLEAN,
  danni_strutturali       BOOLEAN,

  -- Dati estratti
  anno_costruzione        INTEGER,
  stato_conservazione     TEXT,   -- ottimo | buono | mediocre | pessimo
  classe_energetica       TEXT,
  impianti_conformi       BOOLEAN,
  certificato_agibilita   BOOLEAN,

  -- Sintesi
  descrizione_sintetica   TEXT,
  problemi_rilevati       TEXT[],       -- tag array
  punteggio_rischio       INTEGER,      -- 1-10
  note_aggiuntive         TEXT,

  -- Embedding per ricerca semantica
  embedding               vector(1536),

  -- Meta
  modello_ai              TEXT,
  tokens_input            INTEGER,
  tokens_output           INTEGER,
  analizzato_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_asta_id   ON analisi_ai(asta_id);
CREATE INDEX IF NOT EXISTS idx_ai_abuso     ON analisi_ai(abuso_edilizio);
CREATE INDEX IF NOT EXISTS idx_ai_ipoteca   ON analisi_ai(ipoteca_presente);
CREATE INDEX IF NOT EXISTS idx_ai_occupato  ON analisi_ai(occupato_terzi);
CREATE INDEX IF NOT EXISTS idx_ai_rischio   ON analisi_ai(punteggio_rischio);
CREATE INDEX IF NOT EXISTS idx_ai_problemi  ON analisi_ai USING GIN(problemi_rilevati);
CREATE INDEX IF NOT EXISTS idx_ai_embedding ON analisi_ai USING hnsw(embedding vector_cosine_ops);


-- ============================================================
-- TABELLA: scraping_runs (log esecuzioni)
-- ============================================================
CREATE TABLE IF NOT EXISTS scraping_runs (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  mode          TEXT,           -- daily | full | analyze-only
  status        TEXT,           -- running | completed | failed
  started_at    TIMESTAMPTZ DEFAULT NOW(),
  ended_at      TIMESTAMPTZ,
  aste_scraped  INTEGER DEFAULT 0,
  aste_nuove    INTEGER DEFAULT 0,
  aste_errori   INTEGER DEFAULT 0,
  docs_trovati  INTEGER DEFAULT 0,
  note          TEXT
);


-- ============================================================
-- VIEW: v_aste_complete (join principale per l'interfaccia)
-- ============================================================
CREATE OR REPLACE VIEW v_aste_complete AS
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
  -- Analisi AI
  ai.abuso_edilizio,          ai.abuso_edilizio_note,
  ai.sanatoria_pendente,
  ai.vincoli_urbanistici,     ai.vincoli_note,
  ai.difformita_catastale,    ai.difformita_note,
  ai.ipoteca_presente,        ai.ipoteca_importo,
  ai.spese_condominiali_arretrate, ai.spese_condominiali_importo,
  ai.occupato_terzi,          ai.occupazione_note,
  ai.contratto_locazione,     ai.contratto_scadenza,
  ai.necessita_ristrutturazione, ai.livello_ristrutturazione,
  ai.amianto_presente,        ai.danni_strutturali,
  ai.anno_costruzione,        ai.stato_conservazione,
  ai.classe_energetica,       ai.impianti_conformi,
  ai.certificato_agibilita,
  ai.descrizione_sintetica,
  ai.problemi_rilevati,
  ai.punteggio_rischio,
  ai.note_aggiuntive,
  -- Conteggio documenti
  (SELECT COUNT(*) FROM documenti d WHERE d.asta_id = a.id) AS num_documenti,
  (SELECT COUNT(*) FROM documenti d WHERE d.asta_id = a.id AND d.scaricato) AS num_doc_scaricati
FROM aste a
LEFT JOIN analisi_ai ai ON ai.asta_id = a.id
WHERE a.is_active = TRUE;


-- ============================================================
-- FUNZIONE: ricerca semantica via embedding
-- ============================================================
CREATE OR REPLACE FUNCTION search_semantic(
  query_embedding vector(1536),
  match_count     INT   DEFAULT 10,
  min_similarity  FLOAT DEFAULT 0.65
)
RETURNS TABLE (
  pvp_id                TEXT,
  comune                TEXT,
  provincia             TEXT,
  prezzo_base           NUMERIC,
  tipologia             TEXT,
  descrizione_sintetica TEXT,
  problemi_rilevati     TEXT[],
  punteggio_rischio     INTEGER,
  url_dettaglio         TEXT,
  similarity            FLOAT
)
LANGUAGE sql STABLE AS $$
  SELECT
    a.pvp_id, a.comune, a.provincia, a.prezzo_base, a.tipologia,
    ai.descrizione_sintetica, ai.problemi_rilevati, ai.punteggio_rischio,
    a.url_dettaglio,
    1 - (ai.embedding <=> query_embedding) AS similarity
  FROM analisi_ai ai
  JOIN aste a ON a.id = ai.asta_id
  WHERE 1 - (ai.embedding <=> query_embedding) > min_similarity
    AND a.is_active = TRUE
  ORDER BY similarity DESC
  LIMIT match_count;
$$;


-- ============================================================
-- Storage bucket (eseguire separatamente dalla dashboard)
-- ============================================================
-- Vai su Storage → New bucket → nome: "documenti-aste" → Private
-- Oppure via API:
-- INSERT INTO storage.buckets (id, name, public) VALUES ('documenti-aste', 'documenti-aste', false);
