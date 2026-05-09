import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";

export default function TenantNotFoundPage() {
  const router = useRouter();
  const sub =
    typeof router.query.subdomain === "string" ? router.query.subdomain : "";

  return (
    <>
      <Head>
        <title>Tenant not found | DevNest</title>
      </Head>
      <main className="flex min-h-screen flex-col items-center justify-center bg-slate-950 px-6 py-16 text-center text-slate-100">
        <p className="text-sm font-semibold uppercase tracking-[0.3em] text-sky-400">404</p>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight">Tenant not found</h1>
        <p className="mt-4 max-w-md text-sm leading-7 text-slate-300">
          {sub ? (
            <>
              No account is registered for the workspace subdomain <span className="font-mono text-white">{sub}</span>
              . Usernames in URLs are reserved for registered tenants only.
            </>
          ) : (
            <>This tenant subdomain is not registered.</>
          )}
        </p>
        <Link href="/">
          <a className="mt-10 rounded-2xl bg-white px-6 py-3 text-sm font-medium text-slate-950">Back to DevNest</a>
        </Link>
      </main>
    </>
  );
}
