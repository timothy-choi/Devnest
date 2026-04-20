"use client";

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

type NotificationToast = {
  notificationId: number;
  title: string;
  body: string;
  type: string;
  readAt: string | null;
  createdAt: string;
};

type NotificationToastStackProps = {
  notifications: NotificationToast[];
  enabledTypes: Record<string, boolean>;
  inAppEnabled: boolean;
  pushEnabled: boolean;
};

const TOAST_TTL_MS = 6000;

export function NotificationToastStack({
  notifications,
  enabledTypes,
  inAppEnabled,
  pushEnabled,
}: NotificationToastStackProps) {
  const initialized = useRef(false);
  const knownIds = useRef<Set<number>>(new Set());
  const [toasts, setToasts] = useState<NotificationToast[]>([]);

  useEffect(() => {
    if (!initialized.current) {
      notifications.forEach((item) => {
        if (!item.readAt) {
          knownIds.current.add(item.notificationId);
        }
      });
      initialized.current = true;
      return;
    }

    const nextToasts = notifications.filter(
      (item) =>
        inAppEnabled &&
        !item.readAt &&
        Boolean(enabledTypes[item.type]) &&
        !knownIds.current.has(item.notificationId),
    );

    if (!nextToasts.length) {
      return;
    }

    setToasts((current) => {
      const existingIds = new Set(current.map((item) => item.notificationId));
      const additions = nextToasts.filter((item) => !existingIds.has(item.notificationId));
      return [...current, ...additions].slice(-4);
    });

    nextToasts.forEach((item) => {
      knownIds.current.add(item.notificationId);
      if (
        pushEnabled &&
        typeof window !== "undefined" &&
        typeof Notification !== "undefined" &&
        Notification.permission === "granted"
      ) {
        new Notification(item.title, {
          body: item.body,
          tag: `devnest-${item.notificationId}`,
        });
      }
      window.setTimeout(() => {
        setToasts((current) => current.filter((toast) => toast.notificationId !== item.notificationId));
      }, TOAST_TTL_MS);
    });
  }, [enabledTypes, inAppEnabled, notifications, pushEnabled]);

  if (!toasts.length) {
    return null;
  }

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-3">
      {toasts.map((toast) => (
        <div
          key={toast.notificationId}
          className="pointer-events-auto rounded-3xl border border-sky-200 bg-white/95 px-4 py-4 shadow-[0_24px_70px_-40px_rgba(15,23,42,0.55)] backdrop-blur"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <p className="text-sm font-semibold text-slate-950">{toast.title}</p>
              <p className="text-sm leading-6 text-slate-600">{toast.body}</p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 rounded-full"
              onClick={() =>
                setToasts((current) => current.filter((item) => item.notificationId !== toast.notificationId))
              }
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
