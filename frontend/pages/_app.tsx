import type { AppProps } from "next/app";
import Head from "next/head";
import { useEffect } from "react";

import { AppProviders } from "@/app/providers";
import { getAppOrigin } from "@/lib/env";
import "@/styles/globals.css";

export default function DevNestApp({ Component, pageProps }: AppProps) {
  const canonicalOrigin =
    typeof window !== "undefined"
      ? getAppOrigin()
      : null;
  const shouldRedirectToCanonicalOrigin =
    typeof window !== "undefined" &&
    canonicalOrigin !== null &&
    canonicalOrigin !== window.location.origin;

  useEffect(() => {
    if (!shouldRedirectToCanonicalOrigin || canonicalOrigin === null) {
      return;
    }

    const nextUrl = `${canonicalOrigin}${window.location.pathname}${window.location.search}${window.location.hash}`;
    window.location.replace(nextUrl);
  }, [canonicalOrigin, shouldRedirectToCanonicalOrigin]);

  if (shouldRedirectToCanonicalOrigin) {
    return null;
  }

  return (
    <>
      <Head>
        <title>DevNest</title>
        <meta
          name="description"
          content="Persistent browser-based developer workspaces for modern teams."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>
      <AppProviders>
        <Component {...pageProps} />
      </AppProviders>
    </>
  );
}
