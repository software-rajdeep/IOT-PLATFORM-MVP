-- =============================================================================
-- Email alerts: persist Gmail OAuth settings and tokens in the platform DB.
-- Serverless deployments (Vercel) have a read-only, per-instance filesystem, so
-- config/token JSON files cannot survive between invocations. OAuth pending
-- state (with the PKCE verifier) is stored here too, so the callback can land
-- on a different instance than the one that produced the auth URL.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.email_alert_settings (
    company_id UUID PRIMARY KEY REFERENCES core.companies(company_id) ON DELETE CASCADE,
    config     JSONB NOT NULL DEFAULT '{}'::jsonb,
    token      JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.email_oauth_states (
    state         TEXT PRIMARY KEY,
    company_id    UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    code_verifier TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_oauth_states_created
    ON core.email_oauth_states (created_at);
