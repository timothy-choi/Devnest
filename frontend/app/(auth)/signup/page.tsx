import { AuthCard } from "@/components/forms/auth-card";

export default function SignupPage() {
  return (
    <AuthCard
      mode="signup"
      title="Create your DevNest account"
      description="Set up a clean workspace hub for projects, prototypes, and persistent environments."
      submitLabel="Sign Up"
      alternateHref="/login"
      alternateLabel="Already have an account?"
    />
  );
}
