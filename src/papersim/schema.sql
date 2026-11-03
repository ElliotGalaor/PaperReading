-- {dim} est remplacé par settings.embedding_dim au moment de l'init.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS papers (
    id          TEXT PRIMARY KEY,          -- "arxiv:2101.00001", "doi:...", "openalex:W..."
    title       TEXT NOT NULL,
    abstract    TEXT,
    authors     JSONB NOT NULL DEFAULT '[]',
    year        INT,
    venue       TEXT,
    categories  TEXT[] NOT NULL DEFAULT '{}',
    doi         TEXT UNIQUE,
    arxiv_id    TEXT UNIQUE,
    openalex_id TEXT UNIQUE,
    source      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Index plein texte (titre pondéré plus fort que l'abstract).
    tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
    ) STORED
);
CREATE INDEX IF NOT EXISTS papers_tsv_idx ON papers USING gin (tsv);

-- Ajouts ultérieurs (idempotents, pour les bases déjà créées).
ALTER TABLE papers ADD COLUMN IF NOT EXISTS s2_id TEXT UNIQUE;

-- Un embedding par (papier, aspect, modèle).
-- MVP : aspect = 'topic' (titre + abstract). Plus tard : 'problem', 'method', 'limitations'...
CREATE TABLE IF NOT EXISTS paper_embeddings (
    paper_id  TEXT NOT NULL REFERENCES papers (id) ON DELETE CASCADE,
    aspect    TEXT NOT NULL,
    model     TEXT NOT NULL,
    embedding VECTOR({dim}) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (paper_id, aspect, model)
);
CREATE INDEX IF NOT EXISTS paper_embeddings_hnsw_idx
    ON paper_embeddings USING hnsw (embedding vector_cosine_ops);

-- Références sortantes (identifiants OpenAlex) : sert au couplage bibliographique.
CREATE TABLE IF NOT EXISTS paper_references (
    paper_id        TEXT NOT NULL REFERENCES papers (id) ON DELETE CASCADE,
    ref_openalex_id TEXT NOT NULL,
    PRIMARY KEY (paper_id, ref_openalex_id)
);
CREATE INDEX IF NOT EXISTS paper_references_ref_idx ON paper_references (ref_openalex_id);
