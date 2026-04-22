import fs from "node:fs";

import { getApiBaseUrl } from "@/lib/env";

const INTERNAL_KEYS = ["INTERNAL_API_BASE_URL", "API_BASE_URL"] as const;

/**
 * Read compose/runtime-injected env without relying on build-time inlining of
 * ``process.env.INTERNAL_API_BASE_URL`` (Next may bake env at ``next build``).
 */
function readRuntimeEnv(key: string): string {
  if (typeof process === "undefined" || !process.env) {
    return "";
  }
  const v = Reflect.get(process.env, key);
  return typeof v === "string" ? v.trim() : "";
}

/**
 * True when this Node process runs inside a Docker container (Next API routes in compose).
 * Compose sets ``DOCKER_CONTAINER=true``; ``/.dockerenv`` exists in most OCI runtimes.
 */
function isNodeRunningInDocker(): boolean {
  if (process.env.DOCKER_CONTAINER === "true") {
    return true;
  }
  try {
    return fs.existsSync("/.dockerenv");
  } catch {
    return false;
  }
}

function isLoopbackHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

/**
 * Inside a container, ``localhost:8000`` is the frontend container itself, not the API.
 * Host repo-root ``.env`` often sets ``INTERNAL_API_BASE_URL=http://localhost:8000`` for host-side
 * dev — rewrite to the Compose service name so server-side ``fetch`` reaches FastAPI.
 */
function rewriteLoopbackToComposeBackend(base: string): string {
  if (!isNodeRunningInDocker()) {
    return base;
  }
  try {
    const u = new URL(base);
    if (!isLoopbackHostname(u.hostname)) {
      return base.replace(/\/$/, "");
    }
    u.hostname = "backend";
    return u.toString().replace(/\/$/, "");
  } catch {
    return base;
  }
}

/**
 * Base URL for Next.js **server** calls to the FastAPI backend (API routes only).
 * Browsers use `NEXT_PUBLIC_API_BASE_URL` via {@link getApiBaseUrl}.
 *
 * `http://backend:8000` only resolves on the Docker Compose network. If `INTERNAL_API_BASE_URL` is
 * copied to host `.env.local` for `next dev`, DNS fails (`EAI_AGAIN backend`) — we fall back
 * to the public API URL. When `INTERNAL_API_BASE_URL` is unset but we run in Docker, we default
 * to the compose service name.
 */
export function getServerBackendBaseUrl(): string {
  let fromEnv = "";
  for (const key of INTERNAL_KEYS) {
    fromEnv = readRuntimeEnv(key);
    if (fromEnv) {
      break;
    }
  }

  if (fromEnv) {
    const base = fromEnv.replace(/\/$/, "");
    try {
      const { hostname } = new URL(base);
      if (hostname === "backend" && !isNodeRunningInDocker()) {
        return getApiBaseUrl().replace(/\/$/, "");
      }
      return rewriteLoopbackToComposeBackend(base);
    } catch {
      return base;
    }
  }

  if (isNodeRunningInDocker()) {
    return "http://backend:8000";
  }

  return getApiBaseUrl().replace(/\/$/, "");
}
