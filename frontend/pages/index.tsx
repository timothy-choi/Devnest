import type { GetServerSideProps } from "next";

import LandingPage from "@/app/(marketing)/page";
import { getAccessTokenCookieName, getRefreshTokenCookieName } from "@/lib/server/auth-cookies";

export const getServerSideProps: GetServerSideProps = async ({ req }) => {
  const access = req.cookies[getAccessTokenCookieName()];
  const refresh = req.cookies[getRefreshTokenCookieName()];

  if (access || refresh) {
    return {
      redirect: {
        destination: "/dashboard",
        permanent: false,
      },
    };
  }

  return { props: {} };
};

export default LandingPage;
