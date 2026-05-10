#!/usr/bin/env python3
"""Inject a one-time URL cleanup script into code-server's workbench.html (image build).

code-server redirects GET / to /?folder=... when opening the project; VS Code keeps workspace
state after history.replaceState strips those query keys from the visible URL.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOTS = (Path("/usr/lib/code-server"), Path("/usr/share/code-server"))


def _search_roots() -> tuple[Path, ...]:
    extra = (os.environ.get("DEVNEST_PATCH_CODE_SERVER_ROOT") or "").strip()
    if extra:
        return (Path(extra),)
    return ROOTS


SNIPPET = (
    "<script>"
    "(function(){"
    "function s(){"
    "try{"
    "var u=new URL(location.href);"
    "if(!u.searchParams.has('folder')&&!u.searchParams.has('workspace'))return;"
    "u.searchParams.delete('folder');"
    "u.searchParams.delete('workspace');"
    "var q=u.searchParams.toString();"
    "history.replaceState(history.state,'',u.pathname+(q?'?'+q:'')+u.hash);"
    "}catch(e){}"
    "}"
    "s();"
    "[50,200,500,1500,3000,8000].forEach(function(ms){setTimeout(s,ms);});"
    "})();"
    "</script>"
)


def main() -> int:
    html: Path | None = None
    for root in _search_roots():
        if not root.is_dir():
            continue
        for p in root.rglob("workbench.html"):
            if "vs/code/browser/workbench" in str(p).replace("\\", "/"):
                html = p
                break
        if html is not None:
            break
    if html is None:
        print("patch-code-server-workbench: workbench.html not found under /usr/lib|share/code-server", file=sys.stderr)
        return 1
    text = html.read_text(encoding="utf-8")
    marker = "devnest-clean-workbench-url"
    if marker in text:
        print(f"patch-code-server-workbench: already patched {html}")
        return 0
    tagged = SNIPPET.replace("<script>", f"<script>/*{marker}*/", 1)
    new, n = re.subn(r"(?i)</head>", tagged + "</head>", text, count=1)
    if n != 1:
        print(f"patch-code-server-workbench: expected one </head> in {html}, got {n}", file=sys.stderr)
        return 1
    html.write_text(new, encoding="utf-8")
    print(f"patch-code-server-workbench: patched {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
