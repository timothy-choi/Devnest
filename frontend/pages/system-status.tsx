import SystemStatusPage from "@/app/system-status/page";

import { AuthGuard } from "@/components/auth/auth-guard";

export default function SystemStatusRoute() {
  return (
    <AuthGuard>
      <SystemStatusPage />
    </AuthGuard>
  );
}
