import type { GetServerSideProps } from "next";

type OAuthCallbackPageProps = {
  provider: string;
};

export const getServerSideProps: GetServerSideProps<OAuthCallbackPageProps> = async (context) => {
  const provider = typeof context.params?.provider === "string" ? context.params.provider : "";
  const code = typeof context.query.code === "string" ? context.query.code : "";
  const state = typeof context.query.state === "string" ? context.query.state : "";
  const error = typeof context.query.error === "string" ? context.query.error : "";
  const errorDescription =
    typeof context.query.error_description === "string" ? context.query.error_description : "";

  if (!provider) {
    return {
      redirect: {
        destination: "/login?oauth_error=Unsupported%20OAuth%20provider.",
        permanent: false,
      },
    };
  }

  if (error) {
    const detail = errorDescription || error || "OAuth sign-in was cancelled.";
    return {
      redirect: {
        destination: `/login?oauth_error=${encodeURIComponent(detail)}`,
        permanent: false,
      },
    };
  }

  if (!code || !state) {
    return {
      redirect: {
        destination: "/login?oauth_error=OAuth%20callback%20is%20missing%20required%20parameters.",
        permanent: false,
      },
    };
  }

  const params = new URLSearchParams({ code, state }).toString();
  return {
    redirect: {
      destination: `/api/auth/oauth/${encodeURIComponent(provider)}/callback?${params}`,
      permanent: false,
    },
  };
};

export default function OAuthCallbackPage() {
  return null;
}
