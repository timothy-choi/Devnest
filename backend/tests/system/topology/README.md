# Topology V1 system tests (Linux networking)

These tests exercise `DbTopologyAdapter` against the **real host network stack** (`ip`, `nsenter`) and **Docker** (for container network namespaces). They are **not** database-only integration tests.

## Requirements

| Requirement | Why |
|-------------|-----|
| **Linux host** | `ip link` / bridge / veth apply to the machine running pytest (not macOS host with Docker Desktop for bridge visibility). |
| **Docker daemon** | Parent `tests/system/conftest.py` pings Docker; attach tests run `alpine:3.19` briefly. |
| **`CAP_NET_ADMIN`** (or root) | Creating/deleting bridges and moving veth peers requires capability; unprivileged CI users often need `sudo`. |
| **`iproute2`** (`ip`) and **`util-linux`** (`nsenter`) | Same as production V1 topology helpers. |

## Avoiding flaky host port assumptions

Tests assert `internal_endpoint == "{workspace_ip}:8080"` using `WORKSPACE_IDE_CONTAINER_PORT` from the runtime package. They do **not** map container port 8080 to a fixed **host** port; no `-p 8080:8080` is used.

## How to run (only topology system tests)

From the `backend` directory:

```bash
# Serial execution avoids parallel bridge name races if you use pytest-xdist elsewhere
sudo env "PATH=$PATH" pytest tests/system/topology -v -m topology_linux
```

Without `sudo`, tests **skip** if `ip link add … type bridge` fails (see `linux_net_admin_or_skip` in `conftest.py`).

Filter further:

```bash
sudo pytest tests/system/topology/test_topology_v1_linux.py -v -k attach -n0
```

## CI

The GitHub Actions `system-tests` job runs other `tests/system` paths separately, then runs topology tests **as root** so bridges can be created on `ubuntu-latest`.
