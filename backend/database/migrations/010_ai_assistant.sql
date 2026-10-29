-- =============================================================================
-- 010 - Rajdeep AI Assistant: conversation storage.
--
-- The web and WhatsApp channels share the same conversation history. Channel
-- delivery tables (identities, link codes, webhook dedupe, outbox) live in 012.
-- No core.* / metrics.* sensor table is altered. Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.ai_conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    user_id         UUID REFERENCES core.users(id) ON DELETE SET NULL,
    machine_id      VARCHAR(128),
    channel         VARCHAR(16) NOT NULL DEFAULT 'web',   -- web | whatsapp | api
    title           VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.ai_messages (
    message_id      BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES core.ai_conversations(conversation_id) ON DELETE CASCADE,
    role            VARCHAR(16) NOT NULL,                 -- user | assistant
    content         TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_company
    ON core.ai_conversations(company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_user
    ON core.ai_conversations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
    ON core.ai_messages(conversation_id, created_at);
