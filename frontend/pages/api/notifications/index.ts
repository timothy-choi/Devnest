import type { NextApiRequest, NextApiResponse } from "next";

import { backendRequest, readBackendJson } from "@/lib/server/backend-client";
import { forwardJson, sendMethodNotAllowed } from "@/lib/server/http";

type BackendNotificationItem = {
  notification_id: number;
  notification_recipient_id: number;
  type: string;
  title: string;
  body: string;
  priority: string;
  recipient_status: string;
  read_at: string | null;
  dismissed_at: string | null;
  created_at: string;
};

type BackendNotificationList = {
  items: BackendNotificationItem[];
  total: number;
};

function mapItem(item: BackendNotificationItem) {
  return {
    notificationId: item.notification_id,
    notificationRecipientId: item.notification_recipient_id,
    type: item.type,
    title: item.title,
    body: item.body,
    priority: item.priority,
    recipientStatus: item.recipient_status,
    readAt: item.read_at,
    dismissedAt: item.dismissed_at,
    createdAt: item.created_at,
  };
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    sendMethodNotAllowed(res, ["GET"]);
    return;
  }

  const filterMode = typeof req.query.filterMode === "string" ? req.query.filterMode : "all";
  const limit = typeof req.query.limit === "string" ? req.query.limit : "20";

  const response = await backendRequest({
    req,
    res,
    path: `/notifications?filter_mode=${encodeURIComponent(filterMode)}&limit=${encodeURIComponent(limit)}`,
  });
  const data = await readBackendJson<BackendNotificationList | { detail: string }>(response);

  if (!response.ok) {
    forwardJson(res, response.status, data);
    return;
  }

  const list = data as BackendNotificationList;
  res.status(200).json({
    items: list.items.map(mapItem),
    total: list.total,
  });
}
