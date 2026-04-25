# DevNest — Testing Guide

This document describes the test architecture, how to run each test tier, known limitations,
and contribution guidelines.

---

## Wall-clock timeouts (`pytest-timeout`)

Every test must finish within a bounded time. Hanging tests **fail** with a timeout error; **`pytest` continues** with the next test thanks to `--maxfail=0` (no early abort of the whole run).

| Layer | Behavior |
|-------|----------|
| **Plugin** | `pytest-timeout` is in `backend/requirements.txt` and `devnest-gateway/requirements-test.txt`. |
| **Verification** | `python scripts/verify_pytest_timeout.py` (backend) asserts the package is importable **and** `pytest --help` lists `--timeout`. CI runs this before pytest jobs. |
| **Global default** | `backend/pytest.ini`: `timeout = 300` seconds, `timeout_method = thread`, `addopts = -ra --maxfail=0 --timeout-method=thread`. |
| **Unit tests** | `tests/conftest.py` adds `@pytest.mark.timeout(120, method="thread")` to collected tests under `tests/unit/` **unless** they already have `@pytest.mark.timeout`. |
| **Overrides** | Use `@pytest.mark.timeout(N)` on a test (e.g. E2E in `tests/integration/e2e/`) or pass `--timeout=N` on the command line (CLI/inject wins over `pytest.ini` default where applicable; per-test marker takes precedence). |
| **CI guard** | `DEVNEST_ENFORCE_TEST_TIMEOUTS=1` causes `tests/conftest.py` to **abort collection** if the timeout plugin is not loaded. **GitHub Actions** sets this automatically for backend `pytest` invocations when `GITHUB_ACTIONS` is set (in addition to workflow `env`). |
| **Workflow ceiling** | Merge jobs use `timeout-minutes` on the job (e.g. 90–120m) so a stuck runner does not block indefinitely. Nightly full suite uses `timeout-minutes: 180`. |

### Recognizing timeouts in output

- Failed tests show in the short test summary as **`FAILED`** with a message containing `Timeout` / `SIGABRT` / plugin-specific text depending on OS.
- `-ra` adds **skip / xfail / fail / deselected** reasons to the summary so you can distinguish **skipped** vs **failed** vs **timeout** (timeout counts as a failed test case).

### Example commands

```bash
cd backend
pip install -r requirements.txt
python scripts/verify_pytest_timeout.py
export DEVNEST_ENFORCE_TEST_TIMEOUTS=1   # optional: match CI

# Unit (120s default per test via conftest, unless marked)
pytest tests/unit -v --timeout=300 --maxfail=0 -ra

# Integration / system (300s default from pytest.ini unless overridden)
pytest tests/integration -v --timeout=300 --maxfail=0 -ra
pytest tests/system -v --timeout=300 --maxfail=0 -ra

# Nightly-style full backend tree (same as workflow; needs DB/Docker as appropriate)
pytest tests -v --timeout=600 --maxfail=0 -ra

# Gateway package
cd ../devnest-gateway
pip install -r requirements-test.txt
pytest tests -v --timeout=300 --maxfail=0 -ra
```

### Async / Docker / WebSocket / workers

- Prefer **bounded** `asyncio.wait_for`, socket `settimeout`, and fixture `finally` / `join(timeout=…)` for threads (see e.g. SMTP test server fixture).
- If a test starts background workers, cancel or join them in teardown; add **`@pytest.mark.timeout`** if the scenario can still wedge.
- **Pytest continues** after a timeout: one bad test does not stop the rest of the file or suite when `--maxfail=0` (default in `pytest.ini`).

---

## Merge-time vs nightly (CI)

- **Merge-time** (`.github/workflows/tests.yml`): Runs on **every branch push**, **every PR**, and **`workflow_dispatch`** (no `paths` filter on the workflow trigger). Fast PR signal. Pytest excludes markers
  `slow`, `topology_heavy`, `concurrency`, `failure_path`, `topology_linux`, `topology_linux_core`
  for unit/integration/system (with additional system exclusions for `gateway`, `workspace_image`).
  **`pytest-timeout`** is enforced (`--timeout=300`, `--maxfail=0`, `-ra`) on unit, integration, system, and quality jobs.
  A small **stress slice** reruns worker/reconcile/janitor-focused tests in the integration job,
  including **merge production-gate smoke** (`tests/integration/workspace/test_merge_production_gate_smoke.py`)
  and **merge EC2 lifecycle** (`tests/integration/workspace/merge_ec2/` — skips without Docker):
  full create → RUNNING → stop → start → delete on the same stack as the slow EC2 profile test.
- **Nightly** (`.github/workflows/nightly.yml`): Full tree including heavy markers; pytest `--timeout=600 --maxfail=0 -ra` for the main pass; privileged `topology_linux` runs under `sudo` with `--timeout=600` in a follow-up step.
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
```

### Unit tests (no dependencies)

```bash
cd backend
pip install -r requirements.txt   # includes pytest-timeout
python scripts/verify_pytest_timeout.py
export DEVNEST_ENFORCE_TEST_TIMEOUTS=1   # recommended to match CI
pytest tests/unit/ -v --maxfail=0 -ra
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

## Core user flows — E2E / integration coverage

This section maps **product-critical paths** to tests and CI tier. “E2E” here means **HTTP API → persisted jobs → worker/orchestrator outcome → DB state**, not browser automation.

### Already well covered (merge-tier integration)

| Flow | Where | Notes |
|------|--------|--------|
| Register + workspace create + job + RUNNING | `tests/integration/e2e/test_workspace_e2e.py` | Uses **real** `POST /auth/login` for the primary create flow; mocked orchestrator; `@pytest.mark.timeout(15)`. |
| Stop / delete / attach / access / SSE polling | `tests/integration/e2e/test_workspace_e2e.py` | Attach asserts `runtime_ready` so “ready” is not only a status string. |
| Workspace lifecycle (mock orchestrator) | `tests/integration/workspace/test_workspace_lifecycle_api.py` | Broad intent matrix. |
| Repo import API + DB rows | `tests/integration/integrations/test_workspace_repos_api.py` | Uses login; proves 202 + `REPO_IMPORT` job (does not await clone completion). |
| Snapshot HTTP + worker (service / DB) | `tests/integration/snapshots/test_snapshot_api_integration.py`, `test_snapshot_worker_integration.py` | List/create API; worker create+restore with mock orchestrator. |
| Merge EC2 profile lifecycle (Docker present) | `tests/integration/workspace/merge_ec2/test_merge_ec2_profile_lifecycle.py` | create → RUNNING → stop → start → delete; **same** `node_id` / `topology_id` after stop/start (`db_session.expire_all()` after each internal job). |

### New merge-tier proofs (`tests/integration/e2e/test_core_flows_e2e.py`)

| Test | Tier | What it proves |
|------|------|----------------|
| `test_merge_tier_http_login_repo_import_enqueues_job` | **Merge** (`integration`, no `slow`) | Register + **`POST /auth/login`** + `POST .../import-repo` → `REPO_IMPORT` job row. |
| `test_merge_tier_snapshot_create_restore_http_jobs` | **Merge** | Same login path; **HTTP** snapshot create + internal `process` + **HTTP** restore + `SNAPSHOT_RESTORE` success (mock export/import); `@pytest.mark.timeout(90)`. |
| `test_merge_tier_create_failure_not_running_and_job_failed` | **Merge** | Failed bring-up → workspace **ERROR**, job **FAILED** after retries (zero backoff in-test); never RUNNING. |
| `test_merge_tier_stop_failure_creates_durable_cleanup_debt` | **Merge** | Failed stop → **`WorkspaceCleanupTask`** `stop_incomplete` + workspace ERROR. |

### Nightly / slow / system (heavier or real infra)

| Flow | Where | Why not merge-default |
|------|--------|------------------------|
| Full EC2 profile E2E | `tests/integration/workspace/test_workspace_ec2_profile_e2e.py` | Marked **`slow`**; same lifecycle as merge EC2 with `_process_job(..., db_session)` so SQLAlchemy identity map does not lie about `STOPPED`. |
| Real containers / gateway | `tests/system/` | Requires Docker + often `DEVNEST_RUN_SYSTEM_TESTS`; not in default CI. |
| Deferred snapshot system notes | `tests/system/snapshots/test_snapshot_system_deferred.py` | Documents fuller RUNNING → snapshot → STOP → restore path for future system tier. |

### Session / auth beyond MVP

- **Refresh / logout**: covered at the route level in `tests/integration/auth/test_refresh_token_api.py` and `test_logout_api.py`; not duplicated in every workspace E2E file to keep runtime bounded.

### Intentional remaining gaps

| Gap | Reason |
|-----|--------|
| Real `git clone` completion in merge CI | Depends on network + GitHub; merge tier stops at **accepted job + DB row**; full clone belongs in nightly/system with hermetic git or a test double. |
| Browser code-server UX | Out of scope for backend repo; merge tier uses **HTTP** readiness (`runtime_ready`, probes) and system tests for deeper runtime when enabled. |
| Full “process restart” of API | Would require multi-process or k8s-style tests; persistence is proven via **DB + worker** and merge EC2 **stop/start** placement reuse. |

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

Workflow **`.github/workflows/tests.yml`** runs on **all branch pushes**, **all pull requests**, and **`workflow_dispatch`**. It keeps existing gates: **quality-checks**, **unit**, **integration**, **system**, **gateway**, **system-gateway**, **frontend-checks**, and **linux-full-stack-integration** (Compose smoke on the runner). **Nightly** coverage stays in **`nightly.yml`** (unchanged).

### Linux full-stack + EC2 (`tests.yml`)

1. **Path-based `detect`** still skips jobs when a change only touches unrelated areas (same as before).
2. After required jobs succeed, **linux-full-stack-integration** brings up **`docker-compose.integration.yml`**, waits for **`http://localhost:8000/health`** and **`http://localhost:3000/`**, then tears the stack down.
3. **Deploy** (only if secrets are set) uses **`appleboy/ssh-action@v1.2.3`**, runs **`python3 scripts/write_integration_deploy_env.py write`** to emit **`~/Devnest/.env.integration`** (mode **0600**, double-quoted values so long SQLAlchemy URLs are not shell-mangled), then **`scripts/deploy-ec2.sh`** **sources** it, **sync/validates** with the same Python module, and runs **`docker compose --env-file .env.integration`** when using RDS. **SMTP** remains on the SSH shell via **`export`**. **Pull requests never deploy.** **Push to non-`main`** → **deploy-staging**; **push to `main`** → **deploy-production**. **`workflow_dispatch`** follows the same rule using the selected ref.

| Secret | Purpose |
|--------|---------|
| `EC2_HOST` | Public DNS or IPv4 |
| `EC2_USER` | SSH user |
| `EC2_SSH_KEY` | Private key (full multiline PEM/OpenSSH) |
| `DATABASE_URL` | Optional RDS-style Postgres URL; if set, used for `.env.integration` (otherwise **`DEVNEST_DATABASE_URL`** is used) |
| `DEVNEST_DATABASE_URL` | Fallback RDS URL when **`DATABASE_URL`** is unset |
| `DEVNEST_S3_SNAPSHOT_BUCKET` | S3 bucket for workspace snapshots (required when using external Postgres; enforced in **`scripts/deploy-ec2.sh`**) |
| `AWS_REGION` | Region written into `.env.integration` (secret preferred; **`vars.AWS_REGION`** used when the secret is empty) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Optional; included in `.env.integration` only when set. Omit when the EC2 instance uses an **IAM instance profile** with S3 permissions |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | GitHub OAuth app (written into `.env.integration`). If unset, the workflow falls back to **`GH_CLIENT_ID`** / **`GH_CLIENT_SECRET`**, then **`GITHUB_CLIENT_ID`** / **`GITHUB_CLIENT_SECRET`**. |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | Google OAuth web client (same file). If unset, falls back to **`GOOGLE_CLIENT_ID`** / **`GOOGLE_CLIENT_SECRET`**. |

Public UI / OAuth redirect bases (**not** secrets) are derived on the EC2 shell from **`EC2_HOST`**: `http://<dashed-ip>.sslip.io:3000` when the host is an IPv4 address, otherwise `http://<EC2_HOST>:3000`. Those values are written as **`DEVNEST_FRONTEND_PUBLIC_BASE_URL`**, **`GITHUB_OAUTH_PUBLIC_BASE_URL`**, and **`GCLOUD_OAUTH_PUBLIC_BASE_URL`** in `.env.integration` (callbacks: `/auth/oauth/github/callback` and `/auth/oauth/google/callback` under that origin).

| Variable (repo **Settings → Secrets and variables → Actions → Variables**) | Purpose |
|---|---|
| `DEVNEST_S3_SNAPSHOT_PREFIX` | Optional S3 key prefix; default **`devnest-snapshots`** when unset |

**If deploy still fails after setting secrets:** confirm secret **names** match the table, values are **repository** (or organization) secrets visible to the workflow, and you are not storing RDS/S3 only under a GitHub **Environment** that these jobs do not use (`environment:` is not set on deploy jobs). After SSH, **`echo "$DEVNEST_S3_SNAPSHOT_BUCKET"`** is often empty by design (secrets are not left exported on the shell); check **`test -s ~/Devnest/.env.integration`** and deploy logs for **`--- deploy env presence ---`** (presence only).

Missing EC2 SSH secrets → deploy jobs skip; tests can still pass. Job `if` conditions use `env.*` mapped from secrets (GitHub does not allow `secrets.*` in all `if:` contexts).

**Verify a deploy:** open `http://<EC2_HOST>:3000` and `http://<EC2_HOST>:8000/health` after the workflow completes; check the **Deploy to EC2** job log for `git rev-parse HEAD` and `docker compose ps` output from the script.

**Run the stack locally (Linux or Docker Desktop):**

```bash
# From repo root; optional: point the UI at your machine’s API URL when testing from another device
export NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
docker compose -f docker-compose.integration.yml up -d --build
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:3000/
```

**EC2 instance expectations:** Docker and Docker Compose v2; Git; security group allows **TCP 22**, **3000**, and **8000**. Repo on instance: **`~/Devnest`** (clone of **`https://github.com/timothy-choi/Devnest.git`**).

After a successful deploy, the workflow logs print:

- `http://<EC2_HOST>:3000` — web UI  
- `http://<EC2_HOST>:8000` — API  
- `http://<EC2_HOST>:8000/docs` — OpenAPI docs  

The **exact** host is the **`EC2_HOST`** secret; it is not hardcoded in the repo.

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
