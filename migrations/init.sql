-- AXIOM PostgreSQL Schema + pgvector setup
-- Run automatically on first docker compose up

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Skills table ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL UNIQUE,
    description         TEXT NOT NULL,
    tags                JSONB DEFAULT '[]',
    category            TEXT DEFAULT 'unknown',
    input_schema        JSONB DEFAULT '{"fields": [], "description": ""}',
    output_schema       JSONB DEFAULT '{"fields": [], "description": ""}',
    implementation      TEXT DEFAULT '',
    entry_point         TEXT DEFAULT 'run',
    status              TEXT DEFAULT 'draft'
                            CHECK (status IN ('draft','sandbox_pending','sandbox_failed','ready_for_authorization','active','deprecated','flagged')),
    version             TEXT DEFAULT '1.0.0',
    author              TEXT DEFAULT 'axiom-synthesizer',
    hermes_compatible   BOOLEAN DEFAULT TRUE,

    -- Performance metrics
    invocation_count    INTEGER DEFAULT 0,
    success_count       INTEGER DEFAULT 0,
    failure_count       INTEGER DEFAULT 0,
    avg_latency_ms      FLOAT8  DEFAULT 0.0,
    success_rate        FLOAT8  DEFAULT 1.0,

    -- Timestamps
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    last_invoked_at     TIMESTAMPTZ,
    promoted_at         TIMESTAMPTZ,

    -- pgvector embedding (1536-dim for text-embedding-3-small)
    embedding           vector(1536)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- Status-based queries (list, decay monitor)
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills (status);

-- Category filter
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills (category);

-- Tag search (GIN index for jsonb @> operator)
CREATE INDEX IF NOT EXISTS idx_skills_tags ON skills USING GIN (tags);

-- Decay monitor queries
CREATE INDEX IF NOT EXISTS idx_skills_invocation_count ON skills (invocation_count);
CREATE INDEX IF NOT EXISTS idx_skills_last_invoked_at ON skills (last_invoked_at);
CREATE INDEX IF NOT EXISTS idx_skills_success_rate ON skills (success_rate);

-- pgvector HNSW index for fast approximate nearest neighbour search
-- (IVFFlat is also fine for smaller registries)
CREATE INDEX IF NOT EXISTS idx_skills_embedding ON skills
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── Stored procedure: match_skills ───────────────────────────────────────────
-- Used by SkillStore.semantic_search() for vector similarity search
CREATE OR REPLACE FUNCTION match_skills(
    query_embedding vector(1536),
    match_count     INT DEFAULT 10,
    filter_status   TEXT DEFAULT 'active'
)
RETURNS TABLE (
    id                  UUID,
    name                TEXT,
    description         TEXT,
    tags                JSONB,
    category            TEXT,
    input_schema        JSONB,
    output_schema       JSONB,
    implementation      TEXT,
    entry_point         TEXT,
    status              TEXT,
    version             TEXT,
    author              TEXT,
    hermes_compatible   BOOLEAN,
    invocation_count    INTEGER,
    success_count       INTEGER,
    failure_count       INTEGER,
    avg_latency_ms      FLOAT8,
    success_rate        FLOAT8,
    created_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ,
    last_invoked_at     TIMESTAMPTZ,
    promoted_at         TIMESTAMPTZ,
    similarity          FLOAT8
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.id,
        s.name,
        s.description,
        s.tags,
        s.category,
        s.input_schema,
        s.output_schema,
        s.implementation,
        s.entry_point,
        s.status,
        s.version,
        s.author,
        s.hermes_compatible,
        s.invocation_count,
        s.success_count,
        s.failure_count,
        s.avg_latency_ms,
        s.success_rate,
        s.created_at,
        s.updated_at,
        s.last_invoked_at,
        s.promoted_at,
        1 - (s.embedding <=> query_embedding) AS similarity
    FROM skills s
    WHERE s.status = filter_status
      AND s.embedding IS NOT NULL
    ORDER BY s.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ── updated_at trigger ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_skills_updated_at ON skills;
CREATE TRIGGER trigger_skills_updated_at
    BEFORE UPDATE ON skills
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
