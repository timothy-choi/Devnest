import DashboardPage from "@/app/dashboard/page";

import { AuthGuard } from "@/components/auth/auth-guard";

export default function DashboardRoute() {
  return (
    <AuthGuard>
      <DashboardPage />
    </AuthGuard>
  );
}
