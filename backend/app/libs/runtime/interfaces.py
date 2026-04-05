"""Abstract runtime adapter: orchestrator-facing container lifecycle (no workspace semantics)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from .models import ContainerInspectionResult, NetnsRefResult, RuntimeActionResult, RuntimeEnsureResult


class RuntimeAdapter(ABC):
    """
    Container lifecycle abstraction for the orchestrator.

    Implementations translate these calls to Docker/containerd/etc. and return only normalized
    result models. Callers persist ``Workspace_runtime`` (and related state); adapters do not.
    """

    @abstractmethod
    def ensure_container(
        self,
        *,
        name: str,
        image: str,
        cpu_limit: float | None = None,
        memory_limit_bytes: int | None = None,
        env: Mapping[str, str] | None = None,
        ports: Sequence[tuple[int, int]] | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> RuntimeEnsureResult:
        """
        Idempotently ensure a container exists and matches the given spec.

        ``ports`` entries are ``(host_port, container_port)`` publish pairs.
        ``labels`` are engine labels (orchestrator may pass correlation ids without this type knowing workspace rules).
        """

    @abstractmethod
    def start_container(self, *, container_id: str) -> RuntimeActionResult:
        """Start a stopped container."""

    @abstractmethod
    def stop_container(self, *, container_id: str) -> RuntimeActionResult:
        """Stop a running container."""

    @abstractmethod
    def restart_container(self, *, container_id: str) -> RuntimeActionResult:
        """Restart a container."""

    @abstractmethod
    def delete_container(self, *, container_id: str) -> RuntimeActionResult:
        """Remove a container."""

    @abstractmethod
    def inspect_container(self, *, container_id: str) -> ContainerInspectionResult:
        """Return a normalized inspection snapshot (may represent a missing container via ``exists=False``)."""

    @abstractmethod
    def get_container_netns_ref(self, *, container_id: str) -> NetnsRefResult:
        """Resolve pid and network namespace path for topology attach (future phases)."""
