/**
 * Workspace IDE open URL from attach response.
 *
 * **Tenant production:** set ``NEXT_PUBLIC_DEVNEST_WORKSPACE_DOMAIN_MODE=tenant`` so the UI never
 * navigates to ``gateway_url`` (internal ws-* / Traefik-debug URL). Legacy compose/sslip stacks leave
 * this unset or set ``legacy`` to keep gateway fallback when the API only returns ``gateway_url``.
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

/**
 * Browser navigation target after attach. In ``tenant`` mode, returns only ``workspace_url`` /
 * ``public_url`` (never ``gateway_url``).
 */
export function workspaceBrowserOpenUrl(attach: WorkspaceAttachLike): string {
  const ws = (attach.workspace_url || "").trim();
  const pub = (attach.public_url || "").trim();
  const gw = (attach.gateway_url || "").trim();
  const mode = getFrontendWorkspaceDomainMode();
  if (mode === "tenant") {
    return ws || pub;
  }
  if (mode === "legacy") {
    return gw || ws || pub;
  }
  return ws || pub || gw;
}
