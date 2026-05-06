"""Workspace Docker container resource limits and optional security hardening (policy helpers)."""

from __future__ import annotations

from dataclasses import dataclass

from app.libs.common.config import Settings


def parse_cap_drop_setting(raw: str | None) -> tuple[str, ...]:
    """Split comma/whitespace-separated capability names into uppercase tokens."""
    if not raw or not str(raw).strip():
        return ()
    out: list[str] = []
    for tok in str(raw).replace(",", " ").split():
        t = tok.strip().upper()
        if t:
            out.append(t)
    return tuple(out)


@dataclass(frozen=True)
class WorkspaceContainerSecuritySpec:
    """Host security options applied at ``docker create`` / equivalent."""

    security_opt: tuple[str, ...]
    cap_drop: tuple[str, ...]
    read_only_rootfs: bool
    # Snapshot only (not passed as a literal ``security_opt``): ``engine_default`` omits ``seccomp=``.
    seccomp_mode: str = "engine_default"

    def to_applied_dict(self) -> dict[str, object]:
        return {
            "security_opt": list(self.security_opt),
            "cap_drop": list(self.cap_drop),
            "read_only_rootfs": self.read_only_rootfs,
            "seccomp": self.seccomp_mode,
        }


def build_workspace_container_security_spec(settings: Settings) -> WorkspaceContainerSecuritySpec:
    """Derive Docker ``security_opt`` / ``cap_drop`` / read-only root from platform settings."""
    opts: list[str] = []
    seccomp_mode = "engine_default"
    if settings.devnest_workspace_security_no_new_privileges:
        opts.append("no-new-privileges:true")
    # Docker/Moby expects ``seccomp=`` values to be JSON profile bytes or ``unconfined`` — not the word
    # ``default``. Omitting ``seccomp=`` applies the engine default profile.
    if not settings.devnest_workspace_security_seccomp_default:
        opts.append("seccomp=unconfined")
        seccomp_mode = "unconfined"
    caps = parse_cap_drop_setting(settings.devnest_workspace_security_cap_drop)
    return WorkspaceContainerSecuritySpec(
        security_opt=tuple(opts),
        cap_drop=caps,
        read_only_rootfs=bool(settings.devnest_workspace_security_read_only_rootfs),
        seccomp_mode=seccomp_mode,
    )
