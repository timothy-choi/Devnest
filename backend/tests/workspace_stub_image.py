"""Lightweight workspace-runtime stub for Docker-backed tests.

``nginx:alpine`` listens on port 80. DevNest's default container hardening drops
``CAP_NET_BIND_SERVICE``, so binding port 80 fails and the container exits immediately.

``nginxinc/nginx-unprivileged`` listens on 8080 as a non-root user, matching
``WORKSPACE_IDE_CONTAINER_PORT`` and tolerating the default ``cap_drop`` list.
"""

WORKSPACE_STUB_HTTP_IMAGE = "nginxinc/nginx-unprivileged:1.27-alpine"
