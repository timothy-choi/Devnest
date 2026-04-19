import type { GetServerSideProps } from "next";

type OAuthCallbackPageProps = {
  provider: string;
};

function authRouteFromCookie(cookieHeader: string | undefined) {
  const match = (cookieHeader || "").match(/(?:^|;\s*)devnest_oauth_return_to=([^;]+)/);
  const value = match?.[1] ? decodeURIComponent(match[1]) : "";
  return value === "/signup" ? "/signup" : "/login";
}

export const getServerSideProps: GetServerSideProps<OAuthCallbackPageProps> = async (context) => {
  const provider = typeof context.params?.provider === "string" ? context.params.provider : "";
  const code = typeof context.query.code === "string" ? context.query.code : "";
  const state = typeof context.query.state === "string" ? context.query.state : "";
  const error = typeof context.query.error === "string" ? context.query.error : "";
  const errorDescription =
    typeof context.query.error_description === "string" ? context.query.error_description : "";
  const authRoute = authRouteFromCookie(context.req.headers.cookie);

  if (!provider) {
    return {
      redirect: {
        destination: `${authRoute}?oauth_error=Unsupported%20OAuth%20provider.`,
        permanent: false,
      },
    };
  }

  if (error) {
    const detail = errorDescription || error || "OAuth sign-in was cancelled.";
    return {
      redirect: {
        destination: `${authRoute}?oauth_error=${encodeURIComponent(detail)}`,
        permanent: false,
      },
    };
  }

  if (!code || !state) {
    return {
      redirect: {
        destination: `${authRoute}?oauth_error=OAuth%20callback%20is%20missing%20required%20parameters.`,
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
