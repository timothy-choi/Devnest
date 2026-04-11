"""Outbound client for the DevNest data-plane route-admin API (V1)."""

from .errors import GatewayClientError
from .gateway_client import DevnestGatewayClient

__all__ = ["DevnestGatewayClient", "GatewayClientError"]
