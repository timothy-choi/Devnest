import fs from "node:fs";

import { getApiBaseUrl } from "@/lib/env";

/**
 * True when this Node process runs inside a Docker container (Next API routes in compose).
 * Compose can set `DOCKER_CONTAINER=true`; `/.dockerenv` exists in most OCI runtimes.
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
  const fromEnv = (process.env.INTERNAL_API_BASE_URL || process.env.API_BASE_URL || "").trim();
  if (fromEnv) {
    const base = fromEnv.replace(/\/$/, "");
    try {
      const { hostname } = new URL(base);
      if (hostname === "backend" && !isNodeRunningInDocker()) {
        return getApiBaseUrl();
      }
    } catch {
      return base;
    }
    return base;
  }
  if (isNodeRunningInDocker()) {
    return "http://backend:8000";
  }
  return getApiBaseUrl();
}
