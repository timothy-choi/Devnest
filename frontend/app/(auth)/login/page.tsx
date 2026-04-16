import { AuthCard } from "@/components/forms/auth-card";

export default function LoginPage() {
  return (
    <AuthCard
      mode="login"
      title="Welcome back"
      description="Sign in to view your workspaces, recent sessions, and deployment-ready projects."
      submitLabel="Login"
      alternateHref="/signup"
      alternateLabel="Create an account"
    />
  );
}
