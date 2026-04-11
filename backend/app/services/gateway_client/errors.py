class GatewayClientError(Exception):
    """Route-admin HTTP or transport failure (non-fatal for workspace lifecycle)."""


class GatewayClientHTTPError(GatewayClientError):
    """Unexpected HTTP status from route-admin."""


class GatewayClientTransportError(GatewayClientError):
    """Connection / timeout errors reaching route-admin."""
