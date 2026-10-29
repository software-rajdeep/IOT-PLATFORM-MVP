-- =============================================================================
-- 012 - AI assistant delivery channels (WhatsApp linking + outbox).
-- Channel-neutral tables used by app/services/ai_assistant/channels. All rows
-- are scoped to a company; unlinked numbers never receive tenant data.
-- Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.ai_channel_identities (
    identity_id      BIGSERIAL PRIMARY KEY,
    company_id       UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    user_id          UUID REFERENCES core.users(id) ON DELETE SET NULL,
    channel          VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
    external_user_id VARCHAR(191) NOT NULL,
    display_name     VARCHAR(191),
    status           VARCHAR(32) NOT NULL DEFAULT 'active', -- active | opted_out
    opt_in_at        TIMESTAMPTZ,
    opt_out_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel, external_user_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_channel_identities_company
    ON core.ai_channel_identities(company_id, status);

CREATE TABLE IF NOT EXISTS core.ai_link_codes (
    code        VARCHAR(32) PRIMARY KEY,
    company_id  UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    user_id     UUID REFERENCES core.users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_link_codes_company ON core.ai_link_codes(company_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS core.ai_outbox (
    outbox_id       BIGSERIAL PRIMARY KEY,
    company_id      UUID REFERENCES core.companies(company_id) ON DELETE CASCADE,
    channel         VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
    recipient       VARCHAR(191) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'pending', -- pending | retry | sent | failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    sent_at         TIMESTAMPTZ,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_outbox_pending
    ON core.ai_outbox(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS core.ai_webhook_events (
    event_id           BIGSERIAL PRIMARY KEY,
    channel            VARCHAR(32) NOT NULL,
    provider_event_id  VARCHAR(191) NOT NULL,
    payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status             VARCHAR(32) NOT NULL DEFAULT 'pending',
    error              TEXT,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at       TIMESTAMPTZ,
    UNIQUE (channel, provider_event_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_webhook_events_status
    ON core.ai_webhook_events(channel, status, received_at);
