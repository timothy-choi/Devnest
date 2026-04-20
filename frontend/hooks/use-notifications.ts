"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { browserApi } from "@/lib/api/browser-client";
import { ApiError } from "@/lib/api/error";
import { MANAGED_WORKSPACE_NOTIFICATION_TYPES } from "@/lib/notification-types";

type NotificationChannelSettings = {
  inAppEnabled: boolean;
  emailEnabled: boolean;
  pushEnabled: boolean;
};

export function useNotifications() {
  const queryClient = useQueryClient();

  const notificationsQuery = useQuery({
    queryKey: ["notifications", "all"],
    queryFn: () => browserApi.notifications.list("all", 20),
    refetchInterval: 10000,
    retry: false,
  });

  const unreadQuery = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => browserApi.notifications.list("unread", 50),
    refetchInterval: 10000,
    retry: false,
  });

  const preferencesQuery = useQuery({
    queryKey: ["notifications", "preferences"],
    queryFn: () => browserApi.notifications.getPreferences(),
    retry: false,
  });

  const savePreferencesMutation = useMutation({
    mutationFn: ({
      enabledTypes,
      channels,
    }: {
      enabledTypes: Record<string, boolean>;
      channels: NotificationChannelSettings;
    }) =>
      browserApi.notifications.savePreferences({
        preferences: MANAGED_WORKSPACE_NOTIFICATION_TYPES.map((item) => ({
          notificationType: item.type,
          inAppEnabled: Boolean(enabledTypes[item.type]) && channels.inAppEnabled,
          emailEnabled: Boolean(enabledTypes[item.type]) && channels.emailEnabled,
          pushEnabled: Boolean(enabledTypes[item.type]) && channels.pushEnabled,
        })),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["notifications", "preferences"], data);
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: async () => {
      const unreadIds = unreadQuery.data?.items.map((item) => item.notificationId) || [];
      if (!unreadIds.length) {
        return;
      }
      await browserApi.notifications.markReadBulk(unreadIds);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["notifications", "all"] }),
        queryClient.invalidateQueries({ queryKey: ["notifications", "unread"] }),
      ]);
    },
  });

  const enabledTypes = MANAGED_WORKSPACE_NOTIFICATION_TYPES.reduce<Record<string, boolean>>((acc, item) => {
    const pref = preferencesQuery.data?.preferences.find((row) => row.notificationType === item.type);
    acc[item.type] = pref ? pref.inAppEnabled || pref.emailEnabled || pref.pushEnabled : true;
    return acc;
  }, {});

  const channelSettings = MANAGED_WORKSPACE_NOTIFICATION_TYPES.reduce<NotificationChannelSettings>(
    (acc, item) => {
      const pref = preferencesQuery.data?.preferences.find((row) => row.notificationType === item.type);
      acc.inAppEnabled = acc.inAppEnabled && (pref ? pref.inAppEnabled : true);
      acc.emailEnabled = acc.emailEnabled && (pref ? pref.emailEnabled : true);
      acc.pushEnabled = acc.pushEnabled && (pref ? pref.pushEnabled : true);
      return acc;
    },
    { inAppEnabled: true, emailEnabled: true, pushEnabled: true },
  );

  return {
    notifications: notificationsQuery.data?.items || [],
    unreadCount: unreadQuery.data?.total || 0,
    enabledTypes,
    channelSettings,
    isLoading: notificationsQuery.isLoading || preferencesQuery.isLoading,
    errorMessage:
      notificationsQuery.error instanceof ApiError
        ? notificationsQuery.error.detail
        : preferencesQuery.error instanceof ApiError
          ? preferencesQuery.error.detail
          : savePreferencesMutation.error instanceof ApiError
            ? savePreferencesMutation.error.detail
            : null,
    isSavingPreferences: savePreferencesMutation.isLoading,
    isMarkingAllRead: markAllReadMutation.isLoading,
    savePreferences: async (settings: {
      enabledTypes: Record<string, boolean>;
      channels: NotificationChannelSettings;
    }) => {
      await savePreferencesMutation.mutateAsync(settings);
    },
    markAllRead: async () => {
      await markAllReadMutation.mutateAsync();
    },
  };
}
