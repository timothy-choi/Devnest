"""Static checks for the workspace image entrypoint contract."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def test_workspace_entrypoint_forces_auth_argument_and_default_none() -> None:
    text = (_repo_root() / "docker" / "workspace-entrypoint.sh").read_text(encoding="utf-8")
    assert "auth_mode=\"${DEVNEST_WORKSPACE_AUTH_MODE:-${CODE_SERVER_AUTH:-none}}\"" in text
    assert 'code_server_home="${DEVNEST_WORKSPACE_HOME:-/home/coder}"' in text
    assert 'code_server_config="${code_server_config_dir}/config.yaml"' in text
    assert "cat > \"${code_server_config}\" <<EOF" in text
    assert "bind-addr: 0.0.0.0:8080" in text
    assert "auth: ${auth_mode}" in text
    assert "cert: false" in text
    assert "code_server_args=()" in text
    assert "--auth=*)" in text
    assert 'code_server_entrypoint="${DEVNEST_CODE_SERVER_ENTRYPOINT:-/usr/bin/entrypoint.sh}"' in text
    assert "exec \"${code_server_entrypoint}\" --auth \"${auth_mode}\" \"${code_server_args[@]}\"" in text
    assert "unset PASSWORD" in text


def test_workspace_dockerfile_binds_code_server_on_8080_without_password_default() -> None:
    text = (_repo_root() / "Dockerfile.workspace").read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["/usr/local/bin/devnest-workspace-entrypoint.sh"]' in text
    assert 'CMD ["--auth", "none", "--bind-addr", "0.0.0.0:8080", "/home/coder/project"]' in text
    assert "PASSWORD" not in text
