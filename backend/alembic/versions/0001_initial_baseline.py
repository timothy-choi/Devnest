"""Initial baseline schema.

Creates all base tables previously managed by SQLModel.metadata.create_all().
This migration excludes tables introduced in subsequent revisions:
  - workspace_snapshot          (added in 0002)
  - workspace_job.workspace_snapshot_id  (added in 0002)
  - audit_log                   (added in 0003)
  - workspace_usage_record      (added in 0003)
  - policy                      (added in 0005)
  - quota                       (added in 0005)

Existing deployments that were bootstrapped with create_all():
    alembic stamp 0001
    alembic upgrade head

Fresh deployments:
    alembic upgrade head        (runs all revisions from 0001 onward)

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── user_auth ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_auth (
            user_auth_id  SERIAL PRIMARY KEY,
            username      VARCHAR(50)  NOT NULL,
            email         VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            is_verified   BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_user_auth_username UNIQUE (username),
            CONSTRAINT uq_user_auth_email    UNIQUE (email)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_auth_email    ON user_auth (email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_auth_username ON user_auth (username)")

    # ── oauth ────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth (
            oauth_id      SERIAL PRIMARY KEY,
            user_auth_id  INTEGER NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            provider      VARCHAR(32)  NOT NULL,
            provider_uid  VARCHAR(255) NOT NULL,
            access_token  VARCHAR(2048),
            refresh_token VARCHAR(2048),
            expires_at    TIMESTAMPTZ,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_oauth_provider_uid UNIQUE (provider, provider_uid)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_oauth_user_auth_id ON oauth (user_auth_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_oauth_provider     ON oauth (provider)")

    # ── password_reset_token ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_token (
            token_id     SERIAL PRIMARY KEY,
            user_auth_id INTEGER NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            token_hash   VARCHAR(255) NOT NULL,
            expires_at   TIMESTAMPTZ NOT NULL,
            is_used      BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_password_reset_token_user_auth_id ON password_reset_token (user_auth_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_password_reset_token_token_hash   ON password_reset_token (token_hash)")

    # ── token (refresh tokens) ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS token (
            token_id     SERIAL PRIMARY KEY,
            user_auth_id INTEGER      NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            token_hash   VARCHAR(255) NOT NULL,
            expires_at   TIMESTAMPTZ  NOT NULL,
            revoked      BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_token_token_hash UNIQUE (token_hash)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_token_user_auth_id ON token (user_auth_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_token_token_hash   ON token (token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_token_expires_at   ON token (expires_at)")

    # ── user_profile ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_profile_id SERIAL PRIMARY KEY,
            user_auth_id    INTEGER      NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            display_name    VARCHAR(128),
            bio             VARCHAR(4096),
            avatar_url      VARCHAR(1024),
            github_username VARCHAR(128),
            website_url     VARCHAR(1024),
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_user_profile_user_auth_id UNIQUE (user_auth_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_profile_user_auth_id ON user_profile (user_auth_id)")

    # ── user_settings ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_settings_id SERIAL PRIMARY KEY,
            user_auth_id     INTEGER      NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            theme            VARCHAR(32)  NOT NULL DEFAULT 'system',
            editor_font_size INTEGER      NOT NULL DEFAULT 14,
            notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json    JSONB,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_user_settings_user_auth_id UNIQUE (user_auth_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_settings_user_auth_id ON user_settings (user_auth_id)")

    # ── topology ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS topology (
            topology_id     SERIAL PRIMARY KEY,
            name            VARCHAR(128) NOT NULL UNIQUE,
            description     VARCHAR(1024),
            topology_type   VARCHAR(32)  NOT NULL,
            config_json     JSONB        NOT NULL DEFAULT '{}',
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_name          ON topology (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_topology_type ON topology (topology_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_is_active     ON topology (is_active)")

    # ── topology_attachment ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS topology_attachment (
            attachment_id  SERIAL PRIMARY KEY,
            topology_id    INTEGER      NOT NULL REFERENCES topology(topology_id) ON DELETE CASCADE,
            resource_type  VARCHAR(64)  NOT NULL,
            resource_id    VARCHAR(255) NOT NULL,
            attached_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            metadata_json  JSONB
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_attachment_topology_id   ON topology_attachment (topology_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_attachment_resource_type ON topology_attachment (resource_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_attachment_resource_id   ON topology_attachment (resource_id)")

    # ── topology_runtime ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS topology_runtime (
            runtime_id    SERIAL PRIMARY KEY,
            topology_id   INTEGER      NOT NULL REFERENCES topology(topology_id) ON DELETE CASCADE,
            status        VARCHAR(32)  NOT NULL,
            node_id       VARCHAR(255),
            interface     VARCHAR(64),
            ip_address    VARCHAR(64),
            started_at    TIMESTAMPTZ,
            stopped_at    TIMESTAMPTZ,
            metadata_json JSONB,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_runtime_topology_id ON topology_runtime (topology_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_topology_runtime_status      ON topology_runtime (status)")

    # ── ip_allocation ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ip_allocation (
            allocation_id  SERIAL PRIMARY KEY,
            ip_address     VARCHAR(64)  NOT NULL UNIQUE,
            subnet         VARCHAR(64),
            resource_type  VARCHAR(64),
            resource_id    VARCHAR(255),
            allocated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            released_at    TIMESTAMPTZ,
            is_active      BOOLEAN      NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ip_allocation_ip_address   ON ip_allocation (ip_address)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ip_allocation_resource_id  ON ip_allocation (resource_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ip_allocation_is_active    ON ip_allocation (is_active)")

    # ── execution_node ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS execution_node (
            execution_node_id SERIAL PRIMARY KEY,
            node_key          VARCHAR(255) NOT NULL UNIQUE,
            provider          VARCHAR(32)  NOT NULL DEFAULT 'local',
            execution_mode    VARCHAR(32)  NOT NULL DEFAULT 'local',
            status            VARCHAR(32)  NOT NULL DEFAULT 'READY',
            allocatable_cpu   FLOAT        NOT NULL DEFAULT 4.0,
            allocatable_memory_mb INTEGER  NOT NULL DEFAULT 8192,
            reserved_cpu      FLOAT        NOT NULL DEFAULT 0.0,
            reserved_memory_mb INTEGER     NOT NULL DEFAULT 0,
            host              VARCHAR(255),
            ssh_port          INTEGER,
            ssh_user          VARCHAR(64),
            instance_id       VARCHAR(255),
            region            VARCHAR(64),
            instance_type     VARCHAR(64),
            tags_json         JSONB,
            metadata_json     JSONB,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_execution_node_node_key  ON execution_node (node_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_execution_node_status    ON execution_node (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_execution_node_provider  ON execution_node (provider)")

    # ── workspace ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace (
            workspace_id          SERIAL PRIMARY KEY,
            name                  VARCHAR(255) NOT NULL,
            description           VARCHAR(8192),
            owner_user_id         INTEGER NOT NULL REFERENCES user_auth(user_auth_id),
            status                VARCHAR(32)  NOT NULL DEFAULT 'CREATING',
            status_reason         VARCHAR(1024),
            last_error_code       VARCHAR(64),
            last_error_message    VARCHAR(4096),
            endpoint_ref          VARCHAR(512),
            public_host           VARCHAR(512),
            active_sessions_count INTEGER      NOT NULL DEFAULT 0,
            is_private            BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_started          TIMESTAMPTZ,
            last_stopped          TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_owner_user_id ON workspace (owner_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_status        ON workspace (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_name          ON workspace (name)")

    # ── workspace_config ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_config (
            workspace_config_id SERIAL PRIMARY KEY,
            workspace_id        INTEGER      NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            image               VARCHAR(512),
            cpu_request         FLOAT        NOT NULL DEFAULT 0.5,
            memory_mb           INTEGER      NOT NULL DEFAULT 512,
            storage_mb          INTEGER      NOT NULL DEFAULT 2048,
            environment_json    JSONB,
            labels_json         JSONB,
            metadata_json       JSONB,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_workspace_config_workspace_id UNIQUE (workspace_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_config_workspace_id ON workspace_config (workspace_id)")

    # ── workspace_job ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_job (
            workspace_job_id SERIAL PRIMARY KEY,
            workspace_id     INTEGER      NOT NULL REFERENCES workspace(workspace_id),
            job_type         VARCHAR(64)  NOT NULL,
            status           VARCHAR(32)  NOT NULL DEFAULT 'QUEUED',
            attempt_count    INTEGER      NOT NULL DEFAULT 0,
            max_attempts     INTEGER      NOT NULL DEFAULT 2,
            error_message    VARCHAR(4096),
            error_code       VARCHAR(64),
            payload_json     JSONB,
            result_json      JSONB,
            node_key         VARCHAR(255),
            correlation_id   VARCHAR(64),
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            locked_at        TIMESTAMPTZ,
            locked_by        VARCHAR(255),
            completed_at     TIMESTAMPTZ,
            scheduled_after  TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_job_workspace_id  ON workspace_job (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_job_status        ON workspace_job (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_job_job_type      ON workspace_job (job_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_job_created_at    ON workspace_job (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_job_scheduled_after ON workspace_job (scheduled_after)")

    # ── workspace_runtime ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_runtime (
            workspace_runtime_id SERIAL PRIMARY KEY,
            workspace_id         INTEGER      NOT NULL REFERENCES workspace(workspace_id) ON DELETE CASCADE,
            node_key             VARCHAR(255),
            container_id         VARCHAR(255),
            container_name       VARCHAR(255),
            status               VARCHAR(32)  NOT NULL DEFAULT 'PENDING',
            host_port            INTEGER,
            internal_port        INTEGER,
            network_name         VARCHAR(255),
            ip_address           VARCHAR(64),
            started_at           TIMESTAMPTZ,
            stopped_at           TIMESTAMPTZ,
            metadata_json        JSONB,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_runtime_workspace_id ON workspace_runtime (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_runtime_status       ON workspace_runtime (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_runtime_node_key     ON workspace_runtime (node_key)")

    # ── workspace_event ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_event (
            workspace_event_id SERIAL PRIMARY KEY,
            workspace_id       INTEGER      NOT NULL REFERENCES workspace(workspace_id),
            event_type         VARCHAR(64)  NOT NULL,
            actor_user_id      INTEGER      REFERENCES user_auth(user_auth_id),
            message            VARCHAR(4096),
            metadata_json      JSONB,
            correlation_id     VARCHAR(64),
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_event_workspace_id ON workspace_event (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_event_event_type   ON workspace_event (event_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_event_created_at   ON workspace_event (created_at)")

    # ── workspace_session ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS workspace_session (
            workspace_session_id SERIAL PRIMARY KEY,
            workspace_id         INTEGER      NOT NULL REFERENCES workspace(workspace_id),
            user_auth_id         INTEGER      NOT NULL REFERENCES user_auth(user_auth_id),
            session_token_hash   VARCHAR(255) NOT NULL,
            is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
            last_accessed_at     TIMESTAMPTZ,
            expires_at           TIMESTAMPTZ,
            metadata_json        JSONB,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_workspace_session_token_hash UNIQUE (session_token_hash)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_session_workspace_id       ON workspace_session (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_session_user_auth_id       ON workspace_session (user_auth_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_session_session_token_hash ON workspace_session (session_token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workspace_session_is_active          ON workspace_session (is_active)")

    # ── notification ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification (
            notification_id SERIAL PRIMARY KEY,
            title           VARCHAR(255) NOT NULL,
            body            VARCHAR(4096) NOT NULL,
            notification_type VARCHAR(64) NOT NULL,
            priority        VARCHAR(32)  NOT NULL DEFAULT 'normal',
            source          VARCHAR(64),
            source_id       VARCHAR(255),
            metadata_json   JSONB,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_notification_type ON notification (notification_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_created_at        ON notification (created_at)")

    # ── notification_recipient ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_recipient (
            recipient_id    SERIAL PRIMARY KEY,
            notification_id INTEGER      NOT NULL REFERENCES notification(notification_id) ON DELETE CASCADE,
            user_auth_id    INTEGER      NOT NULL REFERENCES user_auth(user_auth_id),
            is_read         BOOLEAN      NOT NULL DEFAULT FALSE,
            read_at         TIMESTAMPTZ,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_recipient_notification_id ON notification_recipient (notification_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_recipient_user_auth_id    ON notification_recipient (user_auth_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_recipient_is_read         ON notification_recipient (is_read)")

    # ── notification_delivery ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_delivery (
            delivery_id     SERIAL PRIMARY KEY,
            notification_id INTEGER      NOT NULL REFERENCES notification(notification_id) ON DELETE CASCADE,
            channel         VARCHAR(32)  NOT NULL,
            status          VARCHAR(32)  NOT NULL DEFAULT 'PENDING',
            attempts        INTEGER      NOT NULL DEFAULT 0,
            last_attempted_at TIMESTAMPTZ,
            delivered_at    TIMESTAMPTZ,
            error_message   VARCHAR(4096),
            metadata_json   JSONB,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_delivery_notification_id ON notification_delivery (notification_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_delivery_channel         ON notification_delivery (channel)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_delivery_status          ON notification_delivery (status)")

    # ── notification_preference ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_preference (
            preference_id   SERIAL PRIMARY KEY,
            user_auth_id    INTEGER      NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            channel         VARCHAR(32)  NOT NULL,
            event_type      VARCHAR(64)  NOT NULL,
            is_enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_notification_preference_user_channel_event UNIQUE (user_auth_id, channel, event_type)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_preference_user_auth_id ON notification_preference (user_auth_id)")

    # ── push_subscription ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS push_subscription (
            subscription_id  SERIAL PRIMARY KEY,
            user_auth_id     INTEGER      NOT NULL REFERENCES user_auth(user_auth_id) ON DELETE CASCADE,
            endpoint         VARCHAR(2048) NOT NULL,
            p256dh           VARCHAR(512),
            auth_key         VARCHAR(512),
            is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_push_subscription_user_auth_id ON push_subscription (user_auth_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_push_subscription_is_active    ON push_subscription (is_active)")


def downgrade() -> None:
    # Drop in reverse dependency order.
    for table in (
        "push_subscription",
        "notification_preference",
        "notification_delivery",
        "notification_recipient",
        "notification",
        "workspace_session",
        "workspace_event",
        "workspace_runtime",
        "workspace_job",
        "workspace_config",
        "workspace",
        "execution_node",
        "ip_allocation",
        "topology_runtime",
        "topology_attachment",
        "topology",
        "user_settings",
        "user_profile",
        "token",
        "password_reset_token",
        "oauth",
        "user_auth",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
