export type CurrentUser = {
  userAuthId: number;
  username: string;
  email: string;
  createdAt: string;
  displayName: string;
  avatarUrl: string | null;
  profileLoaded: boolean;
};
