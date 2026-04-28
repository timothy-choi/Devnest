"""Audit log enumerations."""

from enum import Enum


class AuditActorType(str, Enum):
    """Who performed the action."""

    USER = "user"
    SYSTEM = "system"
    INTERNAL_SERVICE = "internal_service"


class AuditOutcome(str, Enum):
    """Outcome of the audited action."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuditAction(str, Enum):
    """Stable action names recorded in the audit log.

    Prefer the dot-separated ``resource.verb`` convention.
    """

    # ---- Workspace lifecycle ----
    WORKSPACE_CREATE_REQUESTED = "workspace.create.requested"
    WORKSPACE_START_REQUESTED = "workspace.start.requested"
    WORKSPACE_STOP_REQUESTED = "workspace.stop.requested"
    WORKSPACE_RESTART_REQUESTED = "workspace.restart.requested"
    WORKSPACE_DELETE_REQUESTED = "workspace.delete.requested"
    WORKSPACE_UPDATE_REQUESTED = "workspace.update.requested"
    WORKSPACE_JOB_SUCCEEDED = "workspace.job.succeeded"
    WORKSPACE_JOB_FAILED = "workspace.job.failed"

    # ---- Workspace session / attach / access ----
    WORKSPACE_ATTACH_GRANTED = "workspace.attach.granted"
    WORKSPACE_ATTACH_DENIED = "workspace.attach.denied"
    WORKSPACE_ACCESS_GRANTED = "workspace.access.granted"
    WORKSPACE_ACCESS_DENIED = "workspace.access.denied"
    WORKSPACE_SESSION_CREATED = "workspace.session.created"
    WORKSPACE_SESSION_REVOKED = "workspace.session.revoked"

    # ---- Snapshots ----
    WORKSPACE_SNAPSHOT_CREATE_REQUESTED = "workspace.snapshot.create.requested"
    WORKSPACE_SNAPSHOT_CREATED = "workspace.snapshot.created"
    WORKSPACE_SNAPSHOT_CREATE_FAILED = "workspace.snapshot.create.failed"
    WORKSPACE_SNAPSHOT_RESTORE_REQUESTED = "workspace.snapshot.restore.requested"
    WORKSPACE_SNAPSHOT_RESTORED = "workspace.snapshot.restored"
    WORKSPACE_SNAPSHOT_RESTORE_FAILED = "workspace.snapshot.restore.failed"
    WORKSPACE_SNAPSHOT_DELETED = "workspace.snapshot.deleted"

    # ---- Gateway ----
    GATEWAY_ROUTE_REGISTERED = "gateway.route.registered"
    GATEWAY_ROUTE_DEREGISTERED = "gateway.route.deregistered"

    # ---- Reconcile ----
    RECONCILE_STARTED = "reconcile.started"
    RECONCILE_FAILED = "reconcile.failed"

    # ---- Infrastructure / nodes ----
    NODE_PROVISIONED = "node.provisioned"
    NODE_TERMINATED = "node.terminated"
    NODE_REGISTERED = "node.registered"
    NODE_DEREGISTERED = "node.deregistered"

    # ---- Autoscaler ----
    AUTOSCALER_SCALE_UP = "autoscaler.scale_up"
    AUTOSCALER_SCALE_UP_SUPPRESSED = "autoscaler.scale_up.suppressed"
    AUTOSCALER_SCALE_DOWN = "autoscaler.scale_down"
    PLACEMENT_NO_SCHEDULABLE_NODE = "placement.no_schedulable_node"

    # ---- Auth ----
    USER_REGISTERED = "auth.user.registered"
    USER_LOGIN = "auth.user.login"
    USER_LOGOUT = "auth.user.logout"
    USER_DELETED = "auth.user.deleted"
    PASSWORD_CHANGED = "auth.password.changed"

    # ---- Policy and quota enforcement ----
    POLICY_DENIED = "policy.denied"
    QUOTA_EXCEEDED = "quota.exceeded"

    # ---- Product integrations ----
    INTEGRATION_PROVIDER_TOKEN_CONNECTED = "integration.provider_token.connected"
    INTEGRATION_PROVIDER_TOKEN_REVOKED = "integration.provider_token.revoked"
    INTEGRATION_REPO_IMPORTED = "integration.repo.imported"
    INTEGRATION_REPO_IMPORT_FAILED = "integration.repo.import_failed"
    INTEGRATION_GIT_PULL = "integration.git.pull"
    INTEGRATION_GIT_PUSH = "integration.git.push"
    INTEGRATION_CI_TRIGGERED = "integration.ci.triggered"
    INTEGRATION_CI_TRIGGER_FAILED = "integration.ci.trigger_failed"
    INTEGRATION_TERMINAL_SESSION_STARTED = "integration.terminal.session_started"
