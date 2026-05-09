export type CurrentUser = {
  userAuthId: number;
  username: string;
  email: string;
  createdAt: string;
  displayName: string;
  avatarUrl: string | null;
  profileLoaded: boolean;
  /** DNS route label for ``https://<slug>.<public-base-domain>/...`` (may differ from username). */
  routeSubdomainSlug: string | null;
};
