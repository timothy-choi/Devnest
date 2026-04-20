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
  if (req.method !== "PUT") {
    sendMethodNotAllowed(res, ["PUT"]);
    return;
  }

  const body = req.body as { notificationIds: number[] };
  const response = await backendRequest({
    req,
    res,
    path: "/notifications/read-bulk",
    method: "PUT",
    body: {
      notification_ids: body.notificationIds || [],
    },
  });
  const data = await readBackendJson<BackendNotificationItem[] | { detail: string }>(response);
  if (!response.ok) {
    forwardJson(res, response.status, data);
    return;
  }
  res.status(200).json({
    items: (data as BackendNotificationItem[]).map(mapItem),
  });
}
