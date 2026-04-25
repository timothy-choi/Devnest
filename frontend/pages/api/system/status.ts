import type { NextApiRequest, NextApiResponse } from "next";

import { readBackendJson, backendRequest } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

type BackendSnapshot = {
  provider: string;
  bucket?: string;
  prefix?: string;
  region?: string;
  root?: string;
};

type BackendGateway = {
  enabled: boolean;
  base_domain: string;
  public_scheme: string;
  public_port: number;
  auth_enabled: boolean;
  route_admin_host: string;
};

type BackendWorker = {
  deployment_model: string;
  in_process_enabled: boolean;
  in_process_task_running: boolean | null;
  jobs_queued: number;
  jobs_running: number;
};

type BackendApplication = {
  devnest_env: string;
  version: string | null;
  git_commit: string | null;
};

type BackendSystemStatus = {
  backend_ok: boolean;
  database_connected: boolean;
  database_host: string;
  database_name: string;
  snapshot_storage: BackendSnapshot;
  gateway: BackendGateway;
  worker: BackendWorker;
  application: BackendApplication;
  generated_at: string;
};

function mapBody(raw: BackendSystemStatus) {
  return {
    backendOk: raw.backend_ok,
    databaseConnected: raw.database_connected,
    databaseHost: raw.database_host,
    databaseName: raw.database_name,
    snapshotStorage: {
      provider: raw.snapshot_storage.provider,
      bucket: raw.snapshot_storage.bucket ?? "",
      prefix: raw.snapshot_storage.prefix ?? "",
      region: raw.snapshot_storage.region ?? "",
      root: raw.snapshot_storage.root ?? "",
    },
    gateway: {
      enabled: raw.gateway.enabled,
      baseDomain: raw.gateway.base_domain,
      publicScheme: raw.gateway.public_scheme,
      publicPort: raw.gateway.public_port,
      authEnabled: raw.gateway.auth_enabled,
      routeAdminHost: raw.gateway.route_admin_host,
    },
    worker: {
      deploymentModel: raw.worker.deployment_model,
      inProcessEnabled: raw.worker.in_process_enabled,
      inProcessTaskRunning: raw.worker.in_process_task_running,
      jobsQueued: raw.worker.jobs_queued,
      jobsRunning: raw.worker.jobs_running,
    },
    application: {
      devnestEnv: raw.application.devnest_env,
      version: raw.application.version,
      gitCommit: raw.application.git_commit,
    },
    generatedAt: raw.generated_at,
  };
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const upstream = await backendRequest({
    req,
    res,
    path: "/system/status",
  });

  if (!upstream.ok) {
    const errBody = await readBackendJson(upstream);
    forwardJson(res, upstream.status, errBody);
    return;
  }

  const raw = await readBackendJson<BackendSystemStatus>(upstream);
  res.status(200).json(mapBody(raw));
}
