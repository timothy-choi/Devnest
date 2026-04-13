"""Change audit_log.actor_user_id FK to ON DELETE SET NULL.

When a user account is deleted, audit history must be preserved.
Setting actor_user_id to NULL on user deletion retains the audit row while
releasing the FK reference, unblocking account-deletion flows.

Converted from: backend/migrations/manual/003_audit_log_actor_fk_set_null.sql

Revision ID: 0004
Revises: 0003
Create Date: 2025-01-01 00:03:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_actor_user_id_fkey")
    op.execute("""
        ALTER TABLE audit_log
            ADD CONSTRAINT audit_log_actor_user_id_fkey
                FOREIGN KEY (actor_user_id)
                REFERENCES user_auth (user_auth_id)
                ON DELETE SET NULL
    """)


def downgrade() -> None:
    # Revert to the original FK (will fail if any audit rows have actor_user_id pointing to
    # a deleted user; run only on environments where that hasn't happened yet).
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_actor_user_id_fkey")
    op.execute("""
        ALTER TABLE audit_log
            ADD CONSTRAINT audit_log_actor_user_id_fkey
                FOREIGN KEY (actor_user_id)
                REFERENCES user_auth (user_auth_id)
    """)
