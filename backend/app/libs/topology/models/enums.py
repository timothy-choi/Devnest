"""String enums stored as VARCHAR (aligned with ``notification_service.models.enums``)."""

from enum import Enum


class TopologyRuntimeStatus(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    DELETING = "DELETING"


class TopologyAttachmentStatus(str, Enum):
    ATTACHING = "ATTACHING"
    ATTACHED = "ATTACHED"
    DETACHING = "DETACHING"
    DETACHED = "DETACHED"
    FAILED = "FAILED"
