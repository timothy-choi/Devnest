"""Run ``ip`` in the host init PID/network view from inside a Docker sidecar (docker.sock on host).

When the workspace job worker runs in a container with ``docker.sock`` mounted, ``docker inspect``
still reports **host** PIDs for workspace containers (often combined with ``pid: host`` on the
worker). Plain ``subprocess`` ``ip`` then executes in the **sidecar network namespace**, so veth
pairs and bridge plumbing are created in the wrong netns and ``ip link set … netns <pid>`` can
fail with ``Invalid "netns" value`` / EINVAL even though the workspace process is healthy.

Prefixing **only** top-level ``ip`` (not inner ``nsenter … -- ip`` used for workspace netns)
with ``nsenter -t 1 -n`` runs ``ip`` in **PID 1’s network namespace** (the Docker host’s
networking view) while keeping the sidecar’s mount/PID context for the binary. That is enough
for bridge / veth ``ip`` operations on the host.

**Do not** add ``-m`` here: joining the host init **mount** namespace from a default container
fails with ``nsenter: reassociate to namespaces failed: Operation not permitted`` unless the
container is effectively privileged; host netns is what bridge sync actually needs.

Enable with ``DEVNEST_TOPOLOGY_IP_VIA_HOST_NSENTER=1`` (see ``docker-compose.integration.yml``).
"""

from __future__ import annotations

from .command_runner import CommandRunner


class HostPid1NsenterRunner(CommandRunner):
    """
    Wrap a :class:`CommandRunner` so ``ip`` argv lists run under **host PID 1’s network namespace**.

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
            prefixed = ["nsenter", "-t", "1", "-n", "--", *[str(x) for x in cmd]]
            return self._inner.run(prefixed)
        return self._inner.run(cmd)


class HostPid1NsenterProbeRunner(CommandRunner):
    """
    Run **arbitrary** probe commands (``timeout``/``nc``/``curl``/``sh``) in **PID 1's network namespace**.

    Used for IDE reachability checks from a Docker sidecar: topology addresses live on the host
    bridge while the worker process still uses the container's default netns. Unlike
    :class:`HostPid1NsenterRunner`, this wraps **all** argv lists so probes share the same host view
    as ``ip link set … master`` (see ``DEVNEST_TOPOLOGY_IP_VIA_HOST_NSENTER``).

    Commands that already start with ``nsenter`` (e.g. workspace-netns ``nsenter -t <pid> -n``)
    are passed through unchanged.
    """

    def __init__(self, inner: CommandRunner | None = None) -> None:
        self._inner = inner if inner is not None else CommandRunner()

    def run(self, cmd: list[str]) -> str:
        if not cmd:
            raise ValueError("cmd must be a non-empty list of strings")
        if str(cmd[0]) == "nsenter":
            return self._inner.run(cmd)
        prefixed = ["nsenter", "-t", "1", "-n", "--", *[str(x) for x in cmd]]
        return self._inner.run(prefixed)
