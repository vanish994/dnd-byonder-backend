CREATE TABLE campaigns (
    campaign_id UUID PRIMARY KEY,
    ruleset TEXT NOT NULL CHECK (ruleset = 'dnd-2024-phb'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    session_id UUID PRIMARY KEY,
    campaign_id UUID NOT NULL REFERENCES campaigns(campaign_id) ON DELETE RESTRICT,
    session_token_hash BYTEA NOT NULL CHECK (octet_length(session_token_hash) = 32),
    current_revision BIGINT NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, campaign_id)
);
CREATE INDEX sessions_campaign_id_idx ON sessions (campaign_id);

CREATE TABLE campaign_snapshots (
    session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    revision BIGINT NOT NULL CHECK (revision >= 0),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object'),
    state_hash TEXT NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, revision)
);

CREATE TABLE campaign_creation_requests (
    idempotency_key TEXT PRIMARY KEY CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{16,128}$'),
    session_token_hash BYTEA NOT NULL CHECK (octet_length(session_token_hash) = 32),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    campaign_id UUID NOT NULL REFERENCES campaigns(campaign_id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    response JSONB NOT NULL CHECK (jsonb_typeof(response) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id),
    FOREIGN KEY (session_id, campaign_id)
        REFERENCES sessions (session_id, campaign_id) ON DELETE RESTRICT
);

CREATE TABLE campaign_turn_events (
    event_id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9._:-]{16,128}$'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    base_revision BIGINT NOT NULL CHECK (base_revision >= 0),
    result_revision BIGINT NOT NULL CHECK (result_revision = base_revision + 1),
    request JSONB NOT NULL CHECK (jsonb_typeof(request) = 'object'),
    response JSONB NOT NULL CHECK (jsonb_typeof(response) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, idempotency_key),
    UNIQUE (session_id, base_revision),
    FOREIGN KEY (session_id, base_revision)
        REFERENCES campaign_snapshots (session_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (session_id, result_revision)
        REFERENCES campaign_snapshots (session_id, revision) ON DELETE RESTRICT
);
CREATE INDEX campaign_turn_events_created_at_idx ON campaign_turn_events (created_at);

CREATE OR REPLACE FUNCTION reject_campaign_snapshot_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'campaign snapshots are immutable';
END;
$$;
DROP TRIGGER IF EXISTS campaign_snapshots_immutable ON campaign_snapshots;
CREATE TRIGGER campaign_snapshots_immutable
    BEFORE UPDATE OR DELETE ON campaign_snapshots
    FOR EACH ROW EXECUTE FUNCTION reject_campaign_snapshot_mutation();
