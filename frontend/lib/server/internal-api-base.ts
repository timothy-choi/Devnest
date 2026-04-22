import fs from "node:fs";

import { getApiBaseUrl } from "@/lib/env";

const INTERNAL_KEYS = ["INTERNAL_API_BASE_URL", "API_BASE_URL"] as const;

export type ServerBackendUrlSource =
  | "INTERNAL_API_BASE_URL"
  | "API_BASE_URL"
  | "INTERNAL_API_BASE_URL_loopback_rewritten_to_backend"
  | "docker_default_backend_8000"
  | "NEXT_PUBLIC_API_BASE_URL";

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
function rewriteLoopbackToComposeBackend(base: string): { url: string; rewritten: boolean } {
  if (!isNodeRunningInDocker()) {
    return { url: base.replace(/\/$/, ""), rewritten: false };
  }
  try {
    const u = new URL(base);
    if (!isLoopbackHostname(u.hostname)) {
      return { url: base.replace(/\/$/, ""), rewritten: false };
    }
    u.hostname = "backend";
    return { url: u.toString().replace(/\/$/, ""), rewritten: true };
  } catch {
    return { url: base.replace(/\/$/, ""), rewritten: false };
  }
}

export type ServerBackendResolution = {
  baseUrl: string;
  source: ServerBackendUrlSource;
  inDocker: boolean;
};

/**
 * Resolve the FastAPI base URL for Next.js **server** routes (API routes, ``getServerSideProps``).
 *
 * | Mode | Typical result |
 * |------|----------------|
 * | ``next dev`` on host | ``NEXT_PUBLIC_API_BASE_URL`` (e.g. ``http://127.0.0.1:8000``). No ``backend`` hostname — ``docker-compose.dev.yml`` has no API service. |
 * | Frontend container (integration / EC2) | ``INTERNAL_API_BASE_URL`` or default ``http://backend:8000`` on the Compose network. |
 *
 * Browsers use {@link getApiBaseUrl} / ``NEXT_PUBLIC_API_BASE_URL``; this module is server-only.
 */
export function getServerBackendResolution(): ServerBackendResolution {
  const inDocker = isNodeRunningInDocker();
  let fromEnv = "";
  let fromKey: (typeof INTERNAL_KEYS)[number] | null = null;
  for (const key of INTERNAL_KEYS) {
    const v = readRuntimeEnv(key);
    if (v) {
      fromEnv = v;
      fromKey = key;
      break;
    }
  }

  if (fromEnv) {
    const base = fromEnv.replace(/\/$/, "");
    try {
      const { hostname } = new URL(base);
      if (hostname === "backend" && !inDocker) {
        return {
          baseUrl: getApiBaseUrl().replace(/\/$/, ""),
          source: "NEXT_PUBLIC_API_BASE_URL",
          inDocker,
        };
      }
      const { url, rewritten } = rewriteLoopbackToComposeBackend(base);
      if (rewritten && fromKey === "INTERNAL_API_BASE_URL") {
        return { baseUrl: url, source: "INTERNAL_API_BASE_URL_loopback_rewritten_to_backend", inDocker };
      }
      if (fromKey === "INTERNAL_API_BASE_URL") {
        return { baseUrl: url, source: "INTERNAL_API_BASE_URL", inDocker };
      }
      return { baseUrl: url, source: "API_BASE_URL", inDocker };
    } catch {
      return {
        baseUrl: base,
        source: fromKey === "INTERNAL_API_BASE_URL" ? "INTERNAL_API_BASE_URL" : "API_BASE_URL",
        inDocker,
      };
    }
  }

  if (inDocker) {
    return { baseUrl: "http://backend:8000", source: "docker_default_backend_8000", inDocker };
  }

  return {
    baseUrl: getApiBaseUrl().replace(/\/$/, ""),
    source: "NEXT_PUBLIC_API_BASE_URL",
    inDocker,
  };
}

export function getServerBackendBaseUrl(): string {
  return getServerBackendResolution().baseUrl;
}
