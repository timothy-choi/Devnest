"""Unit tests: workspace container security policy helpers."""

from __future__ import annotations

from app.libs.common.config import Settings
from app.libs.runtime.workspace_container_policy import (
    build_workspace_container_security_spec,
    parse_cap_drop_setting,
)


def test_parse_cap_drop_setting_splits_commas() -> None:
    assert parse_cap_drop_setting(" NET_RAW , SYS_ADMIN ") == ("NET_RAW", "SYS_ADMIN")


def test_build_spec_respects_settings_flags() -> None:
    s = Settings(
        devnest_workspace_security_no_new_privileges=True,
        devnest_workspace_security_seccomp_default=True,
        devnest_workspace_security_read_only_rootfs=False,
        devnest_workspace_security_cap_drop="NET_RAW",
    )
    spec = build_workspace_container_security_spec(s)
    assert "no-new-privileges:true" in spec.security_opt
    assert not any(o.startswith("seccomp=") for o in spec.security_opt)
    assert spec.seccomp_mode == "engine_default"
    assert spec.cap_drop == ("NET_RAW",)
    assert spec.read_only_rootfs is False


def test_build_spec_seccomp_off_adds_unconfined() -> None:
    s = Settings(
        devnest_workspace_security_no_new_privileges=False,
        devnest_workspace_security_seccomp_default=False,
        devnest_workspace_security_read_only_rootfs=False,
        devnest_workspace_security_cap_drop="",
    )
    spec = build_workspace_container_security_spec(s)
    assert "seccomp=unconfined" in spec.security_opt
    assert spec.seccomp_mode == "unconfined"


def test_empty_cap_drop_string_means_no_caps() -> None:
    s = Settings(devnest_workspace_security_cap_drop="  ")
    spec = build_workspace_container_security_spec(s)
    assert spec.cap_drop == ()
