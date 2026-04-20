import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

type BackendPreference = {
  preference_id: number;
  notification_type: string;
  in_app_enabled: boolean;
  email_enabled: boolean;
  push_enabled: boolean;
  created_at: string;
  updated_at: string;
};

type BackendPreferencesResponse = {
  preferences: BackendPreference[];
};

function mapPreference(item: BackendPreference) {
  return {
    preferenceId: item.preference_id,
    notificationType: item.notification_type,
    inAppEnabled: item.in_app_enabled,
    emailEnabled: item.email_enabled,
    pushEnabled: item.push_enabled,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === "GET") {
    const response = await backendRequest({
      req,
      res,
      path: "/notifications/preferences",
    });
    const data = await readBackendJson<BackendPreferencesResponse | { detail: string }>(response);
    if (!response.ok) {
      forwardJson(res, response.status, data);
      return;
    }
    const body = data as BackendPreferencesResponse;
    res.status(200).json({
      preferences: body.preferences.map(mapPreference),
    });
    return;
  }

  if (req.method === "PUT") {
    const body = req.body as {
      preferences: Array<{
        notificationType: string;
        inAppEnabled: boolean;
        emailEnabled: boolean;
        pushEnabled: boolean;
      }>;
    };

    const response = await backendRequest({
      req,
      res,
      path: "/notifications/preferences",
      method: "PUT",
      body: {
        preferences: (body.preferences || []).map((item) => ({
          notification_type: item.notificationType,
          in_app_enabled: item.inAppEnabled,
          email_enabled: item.emailEnabled,
          push_enabled: item.pushEnabled,
        })),
      },
    });
    const data = await readBackendJson<BackendPreferencesResponse | { detail: string }>(response);
    if (!response.ok) {
      forwardJson(res, response.status, data);
      return;
    }
    const saved = data as BackendPreferencesResponse;
    res.status(200).json({
      preferences: saved.preferences.map(mapPreference),
    });
    return;
  }

  sendMethodNotAllowed(res, ["GET", "PUT"]);
}
