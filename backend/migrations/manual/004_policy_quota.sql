-- Migration 004: policy and quota tables
--
-- Creates the policy and quota tables introduced in the Policy & Quota Enforcement phase.
-- Run once per environment (dev, staging, prod) after 003_audit_log_actor_fk_set_null.sql.

-- -----------------------------------------------------------------------
-- policy
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy (
    policy_id       SERIAL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL UNIQUE,
    description     VARCHAR(1024),
    policy_type     VARCHAR(32)  NOT NULL,
    scope_type      VARCHAR(32)  NOT NULL,
    scope_id        INTEGER,
    rules_json      JSON         NOT NULL DEFAULT '{}',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL,
    updated_at      TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_policy_name        ON policy (name);
CREATE INDEX IF NOT EXISTS ix_policy_policy_type ON policy (policy_type);
CREATE INDEX IF NOT EXISTS ix_policy_scope_type  ON policy (scope_type);
CREATE INDEX IF NOT EXISTS ix_policy_scope_id    ON policy (scope_id);
CREATE INDEX IF NOT EXISTS ix_policy_is_active   ON policy (is_active);
CREATE INDEX IF NOT EXISTS ix_policy_scope_type_scope_id ON policy (scope_type, scope_id);
CREATE INDEX IF NOT EXISTS ix_policy_is_active_scope     ON policy (is_active, scope_type);

-- -----------------------------------------------------------------------
-- quota
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quota (
    quota_id                SERIAL PRIMARY KEY,
    scope_type              VARCHAR(32)  NOT NULL,
    scope_id                INTEGER,
    max_workspaces          INTEGER      CHECK (max_workspaces >= 0),
    max_running_workspaces  INTEGER      CHECK (max_running_workspaces >= 0),
    max_cpu                 FLOAT,
    max_memory_mb           INTEGER      CHECK (max_memory_mb >= 0),
    max_storage_mb          INTEGER      CHECK (max_storage_mb >= 0),
    max_sessions            INTEGER      CHECK (max_sessions >= 0),
    max_snapshots           INTEGER      CHECK (max_snapshots >= 0),
    max_runtime_hours       FLOAT,
    created_at              TIMESTAMPTZ  NOT NULL,
    updated_at              TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_quota_scope_type           ON quota (scope_type);
CREATE INDEX IF NOT EXISTS ix_quota_scope_id             ON quota (scope_id);
CREATE INDEX IF NOT EXISTS ix_quota_scope_type_scope_id  ON quota (scope_type, scope_id);
