"""Domain errors for the notification service layer."""


class NotificationNotFoundError(Exception):
    """No notification or no access for the given user."""


class DeliveryNotFoundError(Exception):
    """Unknown delivery id."""


class InvalidDeliveryStateError(Exception):
    """Operation not allowed for this delivery status."""


class PushSubscriptionNotFoundError(Exception):
    """Unknown subscription or wrong user."""
