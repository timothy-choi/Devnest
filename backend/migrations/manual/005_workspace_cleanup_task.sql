-- Durable cleanup queue for failed rollbacks and partial stop/detach/IP-release outcomes.
-- Apply to Postgres (production/staging). SQLite test DBs use SQLModel metadata.create_all.

CREATE TABLE IF NOT EXISTS workspace_cleanup_task (
    cleanup_task_id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
    scope VARCHAR(64) NOT NULL,
    detail VARCHAR(8192),
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_workspace_cleanup_workspace_scope UNIQUE (workspace_id, scope)
);

CREATE INDEX IF NOT EXISTS ix_workspace_cleanup_task_workspace_status
    ON workspace_cleanup_task (workspace_id, status);
