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

-- Comptes utilisateurs (mots de passe hachés avec scrypt, jamais en clair).
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_idx ON users (lower(username));

-- Sessions de connexion : seul le hachage du jeton du cookie est stocké.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

-- Favoris, propres à chaque compte.
-- (L'ancienne table « favorites », sans compte, est reprise par le premier compte créé.)
CREATE TABLE IF NOT EXISTS user_favorites (
    user_id    BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    paper_id   TEXT NOT NULL REFERENCES papers (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, paper_id)
);
CREATE INDEX IF NOT EXISTS user_favorites_recent_idx ON user_favorites (user_id, created_at DESC);
