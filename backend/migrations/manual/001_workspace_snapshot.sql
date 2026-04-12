-- Manual migration: workspace snapshots (V1).
-- DevNest normally uses SQLModel.metadata.create_all on startup; use this when evolving an existing
-- Postgres deployment without recreating the database.
--
-- TODO: Adopt Alembic or another migration runner for production schema versioning.

CREATE TABLE IF NOT EXISTS workspace_snapshot (
    workspace_snapshot_id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspace(workspace_id),
    name VARCHAR(255) NOT NULL,
    description VARCHAR(8192),
    storage_uri VARCHAR(1024) NOT NULL,
    status VARCHAR(32) NOT NULL,
    size_bytes INTEGER,
    created_by_user_id INTEGER NOT NULL REFERENCES user_auth(user_auth_id),
    created_at TIMESTAMPTZ NOT NULL,
    metadata_json JSON
);

CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_workspace_id ON workspace_snapshot (workspace_id);
CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_name ON workspace_snapshot (name);
CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_status ON workspace_snapshot (status);
CREATE INDEX IF NOT EXISTS ix_workspace_snapshot_created_by_user_id ON workspace_snapshot (created_by_user_id);

ALTER TABLE workspace_job
    ADD COLUMN IF NOT EXISTS workspace_snapshot_id INTEGER REFERENCES workspace_snapshot(workspace_snapshot_id);

CREATE INDEX IF NOT EXISTS ix_workspace_job_workspace_snapshot_id ON workspace_job (workspace_snapshot_id);
