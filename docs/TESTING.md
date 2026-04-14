# DevNest — Testing Guide

This document describes the test architecture, how to run each test tier, known limitations,
and contribution guidelines.

---

## Merge-time vs nightly (CI)

- **Merge-time** (`.github/workflows/tests.yml`): Fast PR signal. Pytest excludes markers
  `slow`, `topology_heavy`, `concurrency`, `failure_path`, `topology_linux`, `topology_linux_core`
  for unit/integration/system (with additional system exclusions for `gateway`, `workspace_image`).
  **`pytest-timeout`** is enforced (`--timeout=300`) on unit, integration, system, and quality jobs.
  A small **stress slice** reruns worker/reconcile/janitor-focused tests in the integration job,
  including **merge production-gate smoke** (`tests/integration/workspace/test_merge_production_gate_smoke.py`)
  and **merge EC2 lifecycle** (`tests/integration/workspace/merge_ec2/` — skips without Docker):
  full create → RUNNING → stop → start → delete on the same stack as the slow EC2 profile test.
- **Nightly** (`.github/workflows/nightly.yml`): Full tree including heavy markers; pytest  `--timeout=600` for the main pass; privileged `topology_linux` runs under `sudo` in a follow-up step.
- **Heavy / slow examples:** `tests/integration/workspace/test_workspace_ec2_profile_e2e.py` (marked
  `slow`) — full create/stop/start/delete API path; intended for nightly or explicit local runs.

---

## Test Tiers

DevNest uses three test tiers:

| Tier | Directory | Scope | Dependencies |
|---|---|---|---|
| **Unit** | `tests/unit/` | Pure logic, no I/O | None (mocked) |
| **Integration** | `tests/integration/` | DB-backed service logic | PostgreSQL (test DB) |
| **System** | `tests/system/` | Full container lifecycle | Docker + running API |

---

## Running Tests

### Setup

```bash
cd backend
pip install -r requirements.txt
pip install -r requirements-test.txt
```

### Unit tests (no dependencies)

```bash
cd backend
pip install -r requirements.txt   # includes pytest-timeout
python scripts/verify_pytest_timeout.py
pytest tests/unit/ -v
```

Expected: **~804 tests**, all pass. Runtime: < 60 s on a developer machine.

**CI / strict timeouts:** GitHub Actions sets `DEVNEST_ENFORCE_TEST_TIMEOUTS=1` so `tests/conftest.py` aborts configuration if `pytest-timeout` is not loaded. Locally you can export the same after `pip install -r requirements.txt` to match CI.

### Integration tests (requires PostgreSQL)

Start a test database:

```bash
docker run -d --name devnest-test-db \
  -e POSTGRES_DB=devnest_test \
  -e POSTGRES_USER=devnest \
  -e POSTGRES_PASSWORD=devnest \
  -p 5432:5432 \
  postgres:15
```

Set the `TEST_DATABASE_URL` environment variable:

```bash
export TEST_DATABASE_URL=postgresql+psycopg://devnest:devnest@localhost:5432/devnest_test
```

Run integration tests:

```bash
pytest tests/integration/ -v
```

### System tests (requires Docker + running API)

System tests spin up real workspace containers. They require:
- Docker daemon running and accessible.
- `DEVNEST_WORKSPACE_PROJECTS_BASE` pointing to a writable directory.
- A running DevNest API process (or the test fixture starts one).

```bash
pytest tests/system/ -v
```

System tests are skipped in CI unless `DEVNEST_RUN_SYSTEM_TESTS=true` is set.

---

## Test Coverage by Feature

### Distributed Rate Limiting (Task 1)

`tests/unit/rate_limit/test_redis_rate_limiter.py`

| Test | Description |
|---|---|
| `test_fail_open_when_redis_unavailable` | `RedisRateLimiter` allows request on `ConnectionError` |
| `test_increments_and_limits_correctly` | Sorted-set window increments, limit is enforced |
| `test_retry_after_seconds` | `retry_after_seconds` reflects remaining window |
| `test_is_redis_backend_when_redis_backend_set` | Backend selection when `DEVNEST_RATE_LIMIT_BACKEND=redis` |
| `test_is_memory_backend_by_default` | Default backend is memory |

`tests/unit/config/test_redis_config.py`

| Test | Description |
|---|---|
| `test_default_backend_is_memory` | Default `devnest_rate_limit_backend` is `"memory"` |
| `test_redis_url_required_when_distributed_required` | Startup fails when Redis required but URL missing |
| `test_no_error_when_redis_url_set` | No error when `DEVNEST_REDIS_URL` provided |

### /ready Endpoint (Task 8)

`tests/unit/observability/test_ready_endpoint.py`

| Test | Description |
|---|---|
| `test_ready_returns_200_when_db_ok` | DB check passes → 200 |
| `test_ready_returns_503_when_db_fails` | DB check fails → 503 with `failed: ["database"]` |
| `test_ready_includes_redis_check_when_configured` | Redis URL configured → Redis check included |
| `test_ready_503_when_redis_fails` | Redis check fails → 503 |
| `test_health_always_200` | `/health` is independent of checks |

### Workspace Feature Flags (Task 14)

`tests/unit/workspace/test_workspace_features.py`

| Test | Description |
|---|---|
| `test_default_flags_all_false` | All feature flags default to `False` |
| `test_enable_terminal` | Setting `terminal_enabled=True` works |
| `test_roundtrip_via_config_json` | `model_dump` / `model_validate` round-trip |
| `test_features_in_runtime_spec` | `WorkspaceRuntimeSpecSchema` includes `features` |

### CPU/Memory Quotas (Task 6)

`tests/unit/orchestrator/test_orchestrator_code_server.py`

| Test | Description |
|---|---|
| `test_cpu_memory_limits_passed_to_runtime` | `ensure_container` receives `cpu_limit_cores` / `memory_limit_bytes` |

### Autoscaler Drain Delay (Task 4)

`tests/unit/autoscaler/test_autoscaler_drain_delay.py`

| Test | Description |
|---|---|
| `test_find_draining_node_past_delay_returns_node` | Selects DRAINING node past delay |
| `test_find_draining_node_past_delay_too_soon` | Does not select node within delay window |
| `test_no_draining_node_when_none_exist` | Returns `None` when no DRAINING nodes |
| `test_updated_at_none_node_always_drainable` | `updated_at=None` nodes are considered past delay |
| `test_config_has_drain_delay` | `Settings` has `devnest_autoscaler_drain_delay_seconds` |

### code-server Integration (Tasks 12 + 13)

`tests/unit/orchestrator/test_orchestrator_code_server.py`

| Test | Description |
|---|---|
| `test_code_server_env_defaults` | Returns expected default env vars |
| `test_extra_bind_mounts_created` | `_code_server_extra_bind_mounts` returns correct paths |
| `test_host_dirs_created_on_bring_up` | Host directories are created if missing |
| `test_code_server_env_passed_to_runtime` | Bring-up passes env vars to `ensure_container` |

### Snapshot Restore Safety (Task 3)

`tests/unit/orchestrator/test_snapshot_restore_safety.py`

| Test | Description |
|---|---|
| `test_rejects_absolute_path_members` | `_validate_tar_members` rejects `/etc/passwd` |
| `test_rejects_path_traversal` | `_validate_tar_members` rejects `../evil` |
| `test_rejects_device_files` | `_validate_tar_members` rejects `chr`/`blk` members |
| `test_atomic_swap_preserves_original_on_failure` | Original is intact when extraction fails |
| `test_temp_dir_cleaned_up_on_success` | Temp dir removed after successful restore |

---

## Known Limitations

| Limitation | Notes |
|---|---|
| No Docker in unit tests | Orchestrator/runtime tests mock the Docker SDK. Real container behavior is only tested in system tests. |
| In-memory rate limiter not cross-process | Unit tests for `RedisRateLimiter` use a mocked `redis.Redis` instance. |
| Integration tests require a live DB | No in-memory SQLite fallback; schema uses PostgreSQL-specific types. |
| System tests skipped in CI by default | Docker-in-Docker is not configured; set `DEVNEST_RUN_SYSTEM_TESTS=true` to enable. |
| SSE multi-worker (LISTEN/NOTIFY) | Not implemented in P2; no dedicated tests for distributed SSE delivery. |
| Google OAuth feature parity | Partial — Google is sign-in only; repo access tests are GitHub-only. |

---

## CI / GitHub Actions

The CI pipeline runs unit tests on every push and PR:

```yaml
- name: Run unit tests
  run: |
    cd backend
    pytest tests/unit/ -v --tb=short
```

Integration tests run in CI against a PostgreSQL service container:

```yaml
services:
  postgres:
    image: postgres:15
    env:
      POSTGRES_DB: devnest_test
      POSTGRES_USER: devnest
      POSTGRES_PASSWORD: devnest
    ports:
      - 5432:5432
```

---

## Writing New Tests

### Unit tests

- Place in `tests/unit/<service_or_feature>/test_<name>.py`.
- Use `unittest.mock.AsyncMock` / `MagicMock` for async dependencies.
- Never import database sessions; mock the session factory.
- If a function uses lazy imports (e.g. `from app.libs.common.config import get_settings` inside the function body), patch at the definition site (`app.libs.common.config.get_settings`), not the import site.

### Integration tests

- Place in `tests/integration/<service_or_feature>/test_<name>.py`.
- Use the `db_session` pytest fixture (defined in `tests/conftest.py`) for a real DB session.
- Always clean up created rows in a `teardown` or `finally` block.

### Test fixtures

The shared fixtures in `tests/conftest.py` provide:
- `db_session` — a real database session with rollback on teardown
- `mock_settings` — pre-built `Settings` with safe defaults
- `auth_headers` — bearer token headers for authenticated API calls

---

## Test Health Targets

| Tier | Target pass rate | Max runtime |
|---|---|---|
| Unit | 100% | 60 s |
| Integration | 100% | 5 min |
| System | 100% | 15 min |

Flaky tests must be fixed before merging. If a test is genuinely non-deterministic due to
timing, use `pytest-retry` with a maximum of 3 retries and file a follow-up issue.
