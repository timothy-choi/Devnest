import type { NextApiRequest, NextApiResponse } from "next";

import { describeBackendFetchFailure } from "@/lib/server/backend-client";
import { getServerBackendBaseUrl, getServerBackendResolution } from "@/lib/server/internal-api-base";

/**
 * End-to-end probe: Next server → FastAPI (and FastAPI DB/OAuth summary when backend flag is on).
 * Set DEVNEST_AUTH_DIAGNOSTICS=true on **both** backend and frontend, then GET /api/internal/devnest-diagnostics.
 */
export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).end();
  }
  if (process.env.DEVNEST_AUTH_DIAGNOSTICS !== "true") {
    return res.status(404).json({ detail: "Not found" });
  }

  const r = getServerBackendResolution();
  console.info(
    `[DevNest diagnostics] Next GET /api/internal/devnest-diagnostics inDocker=${String(r.inDocker)} baseUrl=${r.baseUrl} source=${r.source}`,
  );

  const base = getServerBackendBaseUrl();
  const probeUrl = `${base}/internal/devnest-auth-diagnostics`;
  let backendHttpStatus: number | null = null;
  let backendFetchError: string | null = null;
  let backendBody: unknown = null;

  try {
    const resp = await fetch(probeUrl, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    backendHttpStatus = resp.status;
    const text = await resp.text();
    try {
      backendBody = text ? JSON.parse(text) : null;
    } catch {
      backendBody = { non_json_preview: text.slice(0, 120) };
    }
  } catch (e) {
    backendFetchError = describeBackendFetchFailure(e);
  }

  return res.status(200).json({
    next_server: {
      in_docker: r.inDocker,
      internal_backend_base_url: r.baseUrl,
      url_resolution_source: r.source,
    },
    backend_probe: {
      requested_path: "/internal/devnest-auth-diagnostics",
      http_status: backendHttpStatus,
      fetch_error_classified: backendFetchError,
      body: backendBody,
    },
  });
}
