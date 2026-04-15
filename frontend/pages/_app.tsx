import type { AppProps } from "next/app";
import Head from "next/head";

import { AppProviders } from "@/app/providers";
import "@/styles/globals.css";

export default function DevNestApp({ Component, pageProps }: AppProps) {
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
