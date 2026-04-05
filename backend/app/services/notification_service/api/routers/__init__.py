from .internal_notifications import router as internal_notifications_router
from .notifications import router as notifications_router

__all__ = ["internal_notifications_router", "notifications_router"]
