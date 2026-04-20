"use client";

import { useEffect, useMemo, useState } from "react";

import { MANAGED_WORKSPACE_NOTIFICATION_TYPES } from "@/lib/notification-types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type NotificationCenterDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  notifications: Array<{
    notificationId: number;
    title: string;
    body: string;
    type: string;
    readAt: string | null;
    createdAt: string;
  }>;
  unreadCount: number;
  enabledTypes: Record<string, boolean>;
  channelSettings: {
    inAppEnabled: boolean;
    emailEnabled: boolean;
    pushEnabled: boolean;
  };
  isLoading?: boolean;
  isSaving?: boolean;
  isMarkingAllRead?: boolean;
  errorMessage?: string | null;
  onSavePreferences: (settings: {
    enabledTypes: Record<string, boolean>;
    channels: {
      inAppEnabled: boolean;
      emailEnabled: boolean;
      pushEnabled: boolean;
    };
  }) => Promise<void>;
  onMarkAllRead: () => Promise<void>;
};

export function NotificationCenterDialog({
  open,
  onOpenChange,
  notifications,
  unreadCount,
  enabledTypes,
  channelSettings,
  isLoading = false,
  isSaving = false,
  isMarkingAllRead = false,
  errorMessage = null,
  onSavePreferences,
  onMarkAllRead,
}: NotificationCenterDialogProps) {
  const [draftEnabledTypes, setDraftEnabledTypes] = useState<Record<string, boolean>>(enabledTypes);
  const [draftChannels, setDraftChannels] = useState(channelSettings);

  useEffect(() => {
    setDraftEnabledTypes(enabledTypes);
    setDraftChannels(channelSettings);
  }, [channelSettings, enabledTypes, open]);

  const allEnabled = useMemo(
    () => MANAGED_WORKSPACE_NOTIFICATION_TYPES.every((item) => draftEnabledTypes[item.type]),
    [draftEnabledTypes],
  );
  const anyEnabled = useMemo(
    () => MANAGED_WORKSPACE_NOTIFICATION_TYPES.some((item) => draftEnabledTypes[item.type]),
    [draftEnabledTypes],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-4 flex max-h-[calc(100vh-2rem)] flex-col translate-y-0 gap-0 overflow-hidden p-0 sm:top-1/2 sm:max-h-[85vh] sm:max-w-2xl sm:-translate-y-1/2">
        <DialogHeader className="border-b border-slate-200/80 px-6 py-5">
          <DialogTitle>Notifications</DialogTitle>
          <DialogDescription>
            Review recent workspace events and choose which lifecycle notifications should appear in the app.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="space-y-6">
            <section className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-950">Recent activity</h3>
                  <p className="text-sm text-slate-500">{unreadCount} unread notification(s)</p>
                </div>
                <Button variant="secondary" size="sm" onClick={onMarkAllRead} disabled={!unreadCount || isMarkingAllRead}>
                  {isMarkingAllRead ? "Marking..." : "Mark all read"}
                </Button>
              </div>

              <div className="space-y-3">
                {notifications.length ? (
                  notifications.map((item) => (
                    <div
                      key={item.notificationId}
                      className={`rounded-2xl border px-4 py-3 ${
                        item.readAt ? "border-slate-200 bg-white" : "border-sky-200 bg-sky-50/70"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="space-y-1">
                          <p className="text-sm font-medium text-slate-950">{item.title}</p>
                          <p className="text-sm leading-6 text-slate-600">{item.body}</p>
                        </div>
                        {!item.readAt ? <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-sky-500" /> : null}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                    No notifications yet.
                  </div>
                )}
              </div>
            </section>

            <section className="space-y-4">
              <div className="flex items-start justify-between gap-4 rounded-3xl border border-slate-200 bg-white px-4 py-4">
                <div className="space-y-1">
                  <p className="font-medium text-slate-900">Workspace lifecycle notifications</p>
                  <p className="text-sm leading-6 text-slate-600">
                    Turn all workspace notifications on or off, then fine-tune the specific event types below.
                  </p>
                  <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">
                    {anyEnabled ? "Notifications enabled" : "Notifications disabled"}
                  </p>
                </div>
                <ToggleSwitch
                  checked={allEnabled}
                  onCheckedChange={(checked) =>
                    setDraftEnabledTypes(
                      MANAGED_WORKSPACE_NOTIFICATION_TYPES.reduce<Record<string, boolean>>((acc, item) => {
                        acc[item.type] = checked;
                        return acc;
                      }, {}),
                    )
                  }
                />
              </div>

              <div className="space-y-3 rounded-3xl border border-slate-200 bg-white px-4 py-4">
                <div className="space-y-1">
                  <p className="font-medium text-slate-900">Delivery methods</p>
                  <p className="text-sm leading-6 text-slate-600">
                    Choose how these workspace notifications should reach you: inside the app, by email, or by push.
                  </p>
                </div>

                <ChannelRow
                  label="In-app"
                  description="Show notifications in the DevNest bell and live popups."
                  checked={draftChannels.inAppEnabled}
                  onCheckedChange={(checked) =>
                    setDraftChannels((current) => ({ ...current, inAppEnabled: checked }))
                  }
                />
                <ChannelRow
                  label="Email"
                  description="Send notification emails when email delivery is configured."
                  checked={draftChannels.emailEnabled}
                  onCheckedChange={(checked) =>
                    setDraftChannels((current) => ({ ...current, emailEnabled: checked }))
                  }
                />
                <ChannelRow
                  label="Push"
                  description="Send push notifications to registered devices or browsers."
                  checked={draftChannels.pushEnabled}
                  onCheckedChange={(checked) =>
                    setDraftChannels((current) => ({ ...current, pushEnabled: checked }))
                  }
                />
              </div>

              <div className="space-y-3">
                {MANAGED_WORKSPACE_NOTIFICATION_TYPES.map((item) => (
                  <div
                    key={item.type}
                    className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white px-4 py-3"
                  >
                    <div>
                      <p className="text-sm font-medium text-slate-900">{item.label}</p>
                      <p className="text-xs text-slate-500">{item.type}</p>
                    </div>
                    <ToggleSwitch
                      checked={Boolean(draftEnabledTypes[item.type])}
                      onCheckedChange={(checked) =>
                        setDraftEnabledTypes((current) => ({
                          ...current,
                          [item.type]: checked,
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            </section>

            {errorMessage ? <p className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{errorMessage}</p> : null}
            {isLoading ? <p className="text-sm text-slate-500">Loading notifications...</p> : null}
          </div>
        </div>

        <div className="border-t border-slate-200/80 bg-white px-6 py-4">
          <DialogFooter className="sm:justify-between">
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Close
            </Button>
            <Button
              type="button"
              onClick={() => onSavePreferences({ enabledTypes: draftEnabledTypes, channels: draftChannels })}
              disabled={isSaving}
            >
              {isSaving ? "Saving..." : "Save preferences"}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ChannelRow({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-100 bg-slate-50/70 px-4 py-3">
      <div className="space-y-1">
        <p className="text-sm font-medium text-slate-900">{label}</p>
        <p className="text-xs leading-5 text-slate-500">{description}</p>
      </div>
      <ToggleSwitch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

function ToggleSwitch({
  checked,
  onCheckedChange,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onCheckedChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition ${
        checked ? "bg-slate-950" : "bg-slate-300"
      }`}
    >
      <span
        className={`mt-0.5 inline-block h-5 w-5 transform rounded-full bg-white transition ${
          checked ? "translate-x-5" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
