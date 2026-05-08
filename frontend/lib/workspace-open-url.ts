/**
 * Workspace IDE open URL from attach response.
 *
 * **Sslip / temporary HTTP edges:** Traefik is usually plain HTTP on e.g. ``:9081``. There is no trusted TLS
 * certificate on ``*.sslip.io``, so the UI must **never** rewrite attach URLs to ``https:`` or derive schemes
 * from ``window.location.protocol``. Production tenant rollout behind Cloudflare / wildcard certs sets
 * ``NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE=tenant`` and backend ``DEVNEST_PUBLIC_SCHEME=https``.
 *
 * **Selection rules:**
 * - ``NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE=tenant`` → ``workspace_url`` / ``public_url`` only (internal
 *   ``gateway_url`` is ws-* debug and must not be used for navigation).
 * - ``legacy`` or unset (default) → prefer ``gateway_url`` first so sslip stacks match the Traefik edge URL
 *   the API intended; then ``workspace_url`` / ``public_url``. Values are passed through **verbatim** to
 *   ``location.assign`` — no protocol normalization.
 */

export type WorkspaceAttachLike = {
  workspace_url?: string | null;
  public_url?: string | null;
  gateway_url?: string | null;
};

/** Mirrors backend ``DEVNEST_WORKSPACE_DOMAIN_MODE``: ``tenant`` | ``legacy`` | unset. */
export function getFrontendWorkspaceDomainMode(): "tenant" | "legacy" | "" {
  const m = (process.env.NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE || "").trim().toLowerCase();
  if (m === "tenant" || m === "legacy") {
    return m;
  }
  return "";
}

function logWorkspaceOpenUrlDecision(
  attach: WorkspaceAttachLike,
  selected: string,
  source: string,
): void {
  if (typeof window === "undefined") {
    return;
  }
  const gw = (attach.gateway_url || "").trim();
  const pub = (attach.public_url || "").trim();
  const ws = (attach.workspace_url || "").trim();
  try {
    console.info(
      "[DevNest workspace open]",
      JSON.stringify({
        frontend_workspace_domain_mode: getFrontendWorkspaceDomainMode() || "unset-legacy-compat",
        selection_source: source,
        selected_url_prefix: selected.slice(0, 48),
        attach_fields_present: {
          gateway_url: Boolean(gw),
          public_url: Boolean(pub),
          workspace_url: Boolean(ws),
        },
      }),
    );
  } catch {
    /* ignore logging failures */
  }
}

/**
 * Browser navigation target after attach. Does not modify URL strings (no https upgrade).
 */
export function workspaceBrowserOpenUrl(attach: WorkspaceAttachLike): string {
  const ws = (attach.workspace_url || "").trim();
  const pub = (attach.public_url || "").trim();
  const gw = (attach.gateway_url || "").trim();
  const mode = getFrontendWorkspaceDomainMode();

  if (mode === "tenant") {
    const chosen = ws || pub;
    logWorkspaceOpenUrlDecision(attach, chosen, "tenant:workspace_url|public_url_only");
    return chosen;
  }

  // Legacy / default: Traefik edge URL first (sslip HTTP); API keeps gateway_url aligned with registration.
  const chosen = gw || ws || pub;
  logWorkspaceOpenUrlDecision(
    attach,
    chosen,
    mode === "legacy" ? "legacy:gateway_url|workspace_url|public_url" : "default-legacy:gateway_url|workspace_url|public_url",
  );
  return chosen;
}
