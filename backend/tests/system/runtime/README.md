# System tests: Runtime adapter (real Docker)

These tests exercise `DockerRuntimeAdapter` against a **real** local Docker engine. They do **not** mock `docker-py`.

## Prerequisites

- Docker daemon running and reachable (`docker info` works).
- Pull access for the test image (default: `nginx:alpine`), or use a locally available image via `DEVNEST_RUNTIME_SYSTEM_IMAGE`.

## Run only runtime system tests

From the `backend/` directory:

```bash
pytest tests/system/runtime/ -v -m system
```

Or a single file:

```bash
pytest tests/system/runtime/test_docker_runtime_system.py -v
```

## Configuration

| Variable | Purpose |
|----------|---------|
| `DEVNEST_RUNTIME_SYSTEM_IMAGE` | Image used for create/start (default `nginx:alpine`). Must stay up with its default `CMD` when started; the adapter bind-mounts a temp directory to `/home/coder/project`. Tests request host publish via explicit `ports=((0, 8080),)` (ephemeral host port for in-container 8080). |

## Isolation and cleanup

- Each test uses a unique container name and a temporary workspace directory.
- The `isolated_runtime` fixture always attempts to remove the container (force) and delete the temp directory in a `finally` block, including after failures.

## CI note

The default GitHub Actions job runs `pytest tests -v`, which includes these tests if Docker is available on the runner.
