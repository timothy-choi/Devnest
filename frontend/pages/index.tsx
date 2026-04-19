import type { GetServerSideProps } from "next";

import LandingPage from "@/app/(marketing)/page";
import { GuestOnly } from "@/components/auth/guest-only";

export const getServerSideProps: GetServerSideProps = async () => {
  return { props: {} };
};

export default function IndexPage() {
  return (
    <GuestOnly>
      <LandingPage />
    </GuestOnly>
  );
}
