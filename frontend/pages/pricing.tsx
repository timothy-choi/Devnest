import Head from "next/head";
import Link from "next/link";

import { GuestOnly } from "@/components/auth/guest-only";

export default function PricingPage() {
  return (
    <GuestOnly>
      <>
        <Head>
          <title>Pricing | DevNest</title>
        </Head>
        <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-16">
          <div>
            <p className="text-sm font-medium uppercase tracking-[0.25em] text-sky-700">Pricing</p>
            <h1 className="mt-2 text-3xl font-semibold text-slate-950">Plans for teams</h1>
            <p className="mt-3 text-slate-600">
              DevNest pricing will appear here. Marketing and account flows stay on the apex domain.
            </p>
          </div>
          <Link href="/">
            <a className="text-sm font-medium text-sky-700 underline-offset-4 hover:underline">← Home</a>
          </Link>
        </main>
      </>
    </GuestOnly>
  );
}
