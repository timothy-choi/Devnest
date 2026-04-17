"""Run ``ip`` in the host init PID/network view from inside a Docker sidecar (docker.sock on host).

When the workspace job worker runs in a container with ``docker.sock`` mounted, ``docker inspect``
still reports **host** PIDs for workspace containers (often combined with ``pid: host`` on the
worker). Plain ``subprocess`` ``ip`` then executes in the **sidecar network namespace**, so veth
pairs and bridge plumbing are created in the wrong netns and ``ip link set … netns <pid>`` can
fail with ``Invalid "netns" value`` / EINVAL even though the workspace process is healthy.

Prefixing **only** top-level ``ip`` (not inner ``nsenter … -- ip`` used for workspace netns)
with ``nsenter -t 1 -m -n -p`` runs the tool in the same namespaces as PID 1 on the machine,
which matches where the Docker daemon applies workspace networking on single-host integration /
typical EC2 + Docker setups.

Enable with ``DEVNEST_TOPOLOGY_IP_VIA_HOST_NSENTER=1`` (see ``docker-compose.integration.yml``).
"""

from __future__ import annotations

from .command_runner import CommandRunner


class HostPid1NsenterRunner(CommandRunner):
    """
    Wrap a :class:`CommandRunner` so ``ip`` argv lists are executed under host init namespaces.

    Commands that already start with ``nsenter`` are passed through unchanged (workspace-netns
    helpers build ``nsenter -n -t <workspace_pid> -- ip …``).
    """

    def __init__(self, inner: CommandRunner | None = None) -> None:
        self._inner = inner if inner is not None else CommandRunner()

    def run(self, cmd: list[str]) -> str:
        if not cmd:
            raise ValueError("cmd must be a non-empty list of strings")
        head = str(cmd[0])
        if head == "nsenter":
            return self._inner.run(cmd)
        if head == "ip":
            prefixed = ["nsenter", "-t", "1", "-m", "-n", "-p", "--", *[str(x) for x in cmd]]
            return self._inner.run(prefixed)
        return self._inner.run(cmd)
