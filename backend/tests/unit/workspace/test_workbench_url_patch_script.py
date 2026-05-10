"""Sanity-check the workspace image workbench URL patch script."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def test_patch_script_inserts_marker_into_head() -> None:
    script = _repo_root() / "docker" / "patch-code-server-workbench-clean-url.py"
    src = textwrap.dedent(
        """\
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"></head><body></body></html>
        """
    )
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "usr/lib/code-server/lib/vscode/out/vs/code/browser/workbench"
        root.mkdir(parents=True)
        html = root / "workbench.html"
        html.write_text(src, encoding="utf-8")
        r = subprocess.run(
            ["python3", str(script)],
            cwd=td,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "DEVNEST_PATCH_CODE_SERVER_ROOT": td},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        out = html.read_text(encoding="utf-8")
        assert "devnest-clean-workbench-url" in out
        assert "history.replaceState" in out
        assert "</script></head>" in out.replace("\n", "")


def test_dockerfile_invokes_patch_script() -> None:
    text = (_repo_root() / "Dockerfile.workspace").read_text(encoding="utf-8")
    assert "patch-code-server-workbench-clean-url.py" in text
    assert "python3 /tmp/patch-code-server-workbench-clean-url.py" in text
