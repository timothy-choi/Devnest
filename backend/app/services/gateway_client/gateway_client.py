"""HTTP client for devnest-gateway route-admin (control plane → data plane, V1)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.libs.common.config import Settings
from app.libs.observability.correlation import get_correlation_id
from app.libs.observability.log_events import LogEvent, log_event
from app.libs.observability.metrics import record_gateway_operation

from .errors import GatewayClientHTTPError, GatewayClientTransportError
from .schemas import GatewayRouteRegisterPayload

logger = logging.getLogger(__name__)


def _normalize_target(internal_endpoint: str) -> str:
    t = (internal_endpoint or "").strip()
    if not t:
        raise ValueError("internal_endpoint is empty")
    if "://" not in t:
        t = f"http://{t}"
    if not (t.startswith("http://") or t.startswith("https://")):
        raise ValueError("internal_endpoint must be http(s) URL or host:port")
    return t


class DevnestGatewayClient:
    """Idempotent register/deregister against route-admin. See devnest-gateway/route_admin/."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = (base_url or "").strip().rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> DevnestGatewayClient:
        return cls(settings.devnest_gateway_url, timeout_s=10.0)

    def _client_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"base_url": self._base, "timeout": self._timeout}
        if self._transport is not None:
            kw["transport"] = self._transport
        return kw

    def _correlation_headers(self) -> dict[str, str]:
        cid = get_correlation_id()
        return {"X-Correlation-ID": cid} if cid else {}

    def register_route(
        self,
        workspace_id: str,
        internal_endpoint: str,
        public_host: str,
        *,
        node_key: str | None = None,
        execution_node_id: int | None = None,
    ) -> None:
        wid = str(workspace_id).strip()
        host = (public_host or "").strip()
        if not wid or not host:
            raise ValueError("workspace_id and public_host are required")
        target = _normalize_target(internal_endpoint)
        nk = (node_key or "").strip() or None
        payload = GatewayRouteRegisterPayload(
            workspace_id=wid,
            public_host=host,
            target=target,
            node_key=nk,
            execution_node_id=execution_node_id,
        ).model_dump(exclude_none=True)
        c = httpx.Client(**self._client_kwargs())
        try:
            try:
                r = c.post("/routes", json=payload, headers=self._correlation_headers())
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                record_gateway_operation(operation="register", success=False)
                logger.warning(
                    "gateway_register_http_error",
                    extra={
                        "workspace_id": wid,
                        "status_code": e.response.status_code,
                        "detail": (e.response.text or "")[:500],
                    },
                )
                raise GatewayClientHTTPError(
                    f"route-admin POST /routes failed: {e.response.status_code}",
                ) from e
            except httpx.RequestError as e:
                record_gateway_operation(operation="register", success=False)
                logger.warning(
                    "gateway_register_transport_error",
                    extra={"workspace_id": wid, "error": str(e)},
                )
                raise GatewayClientTransportError(f"route-admin unreachable: {e}") from e
            record_gateway_operation(operation="register", success=True)
            log_event(
                logger,
                LogEvent.GATEWAY_ROUTE_REGISTERED,
                workspace_id=wid,
                public_host=host,
                node_key=nk,
                execution_node_id=execution_node_id,
                gateway_upstream_target=target,
            )
        finally:
            c.close()

    def deregister_route(self, workspace_id: str) -> None:
        wid = str(workspace_id).strip()
        if not wid:
            raise ValueError("workspace_id is empty")
        c = httpx.Client(**self._client_kwargs())
        try:
            try:
                r = c.delete(f"/routes/{wid}", headers=self._correlation_headers())
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                record_gateway_operation(operation="deregister", success=False)
                logger.warning(
                    "gateway_deregister_http_error",
                    extra={
                        "workspace_id": wid,
                        "status_code": e.response.status_code,
                        "detail": (e.response.text or "")[:500],
                    },
                )
                raise GatewayClientHTTPError(
                    f"route-admin DELETE /routes/{wid} failed: {e.response.status_code}",
                ) from e
            except httpx.RequestError as e:
                record_gateway_operation(operation="deregister", success=False)
                logger.warning(
                    "gateway_deregister_transport_error",
                    extra={"workspace_id": wid, "error": str(e)},
                )
                raise GatewayClientTransportError(f"route-admin unreachable: {e}") from e
            record_gateway_operation(operation="deregister", success=True)
            log_event(logger, LogEvent.GATEWAY_ROUTE_DEREGISTERED, workspace_id=wid)
        finally:
            c.close()

    def get_registered_routes(self) -> list[dict[str, Any]]:
        """GET /routes — debugging / operators."""
        c = httpx.Client(**self._client_kwargs())
        try:
            try:
                r = c.get("/routes")
                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                raise GatewayClientHTTPError(
                    f"route-admin GET /routes failed: {e.response.status_code}",
                ) from e
            except httpx.RequestError as e:
                raise GatewayClientTransportError(f"route-admin unreachable: {e}") from e
            if not isinstance(data, list):
                raise GatewayClientHTTPError("route-admin GET /routes returned non-list JSON")
            return data
        finally:
            c.close()
