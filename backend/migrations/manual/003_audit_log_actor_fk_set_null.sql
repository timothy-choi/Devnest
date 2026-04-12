-- Migration 003: change audit_log.actor_user_id FK to ON DELETE SET NULL
--
-- Rationale: when a user account is deleted, audit history must be preserved.
-- Setting actor_user_id to NULL on deletion retains the audit row while
-- releasing the FK reference, and unblocks account-deletion flows that
-- commit the user deletion before or alongside the audit INSERT.
--
-- Run once against each environment (dev, staging, prod).

ALTER TABLE audit_log
    DROP CONSTRAINT IF EXISTS audit_log_actor_user_id_fkey;

ALTER TABLE audit_log
    ADD CONSTRAINT audit_log_actor_user_id_fkey
        FOREIGN KEY (actor_user_id)
        REFERENCES user_auth (user_auth_id)
        ON DELETE SET NULL;
