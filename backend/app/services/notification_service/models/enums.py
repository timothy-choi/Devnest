"""String enums stored as VARCHAR in the database."""

from enum import Enum


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_SENT = "PARTIALLY_SENT"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RecipientStatus(str, Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    READ = "READ"
    DISMISSED = "DISMISSED"


class DeliveryChannel(str, Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    PUSH = "PUSH"


class DeliveryStatus(str, Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PushPlatform(str, Enum):
    WEB = "WEB"
    IOS = "IOS"
    ANDROID = "ANDROID"
