CREATE TABLE aw_team_settings (
    scope text NOT NULL,
    team text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    document jsonb NOT NULL,
    PRIMARY KEY (scope, team)
);
CREATE TABLE aw_api_keys (
    scope text NOT NULL,
    key_id text NOT NULL,
    team text NOT NULL,
    digest text NOT NULL,
    document jsonb NOT NULL,
    PRIMARY KEY (scope, key_id),
    UNIQUE (scope, digest)
);
CREATE INDEX aw_api_keys_team ON aw_api_keys (scope, team);
CREATE TABLE aw_start_intents (
    scope text NOT NULL,
    workflow_id text NOT NULL,
    document jsonb NOT NULL,
    expires_at double precision,
    PRIMARY KEY (scope, workflow_id)
);
CREATE INDEX aw_start_intents_expiry ON aw_start_intents (scope, expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE aw_runs (
    scope text NOT NULL,
    team text NOT NULL,
    run_id text NOT NULL,
    project text NOT NULL,
    created_at double precision NOT NULL,
    document jsonb NOT NULL,
    snapshot jsonb,
    expires_at double precision,
    PRIMARY KEY (scope, team, run_id)
);
CREATE INDEX aw_runs_page ON aw_runs (scope, team, project, created_at DESC, run_id COLLATE "C" DESC);
CREATE INDEX aw_runs_expiry ON aw_runs (scope, expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE aw_run_steps (
    id bigserial PRIMARY KEY,
    scope text NOT NULL,
    team text NOT NULL,
    run_id text NOT NULL,
    event json NOT NULL,
    FOREIGN KEY (scope, team, run_id) REFERENCES aw_runs ON DELETE CASCADE
);
CREATE INDEX aw_run_steps_run ON aw_run_steps (scope, team, run_id, id);
CREATE TABLE aw_audit_events (
    id bigserial PRIMARY KEY,
    scope text NOT NULL,
    team text,
    chain_id text NOT NULL,
    sequence bigint NOT NULL,
    stored_at double precision NOT NULL DEFAULT extract(epoch FROM clock_timestamp()),
    event json NOT NULL,
    entry json,
    UNIQUE (scope, chain_id, sequence)
);
CREATE INDEX aw_audit_team ON aw_audit_events (scope, team, id DESC);
CREATE INDEX aw_audit_expiry ON aw_audit_events (scope, stored_at);
CREATE TABLE aw_audit_heads (
    scope text NOT NULL,
    chain_id text NOT NULL,
    head text NOT NULL,
    count bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (scope, chain_id)
);
