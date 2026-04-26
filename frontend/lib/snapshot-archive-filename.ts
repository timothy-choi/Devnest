/**
 * Parse Content-Disposition filename for snapshot archive downloads.
 * Must never throw — malformed headers or bad percent-encoding must fall back.
 */
export function parseSnapshotArchiveFilename(contentDisposition: string | null, fallback: string): string {
  if (!contentDisposition) {
    return fallback;
  }
  const cd = contentDisposition.trim();
  // RFC 5987 first, then quoted, then token form (avoid greedy filename= matching a quoted value badly).
  const rfc5987 = /\bfilename\*=(?:UTF-8''|)([^;\s]+)/i.exec(cd);
  if (rfc5987?.[1]) {
    const raw = rfc5987[1].trim().replace(/^"|"$/g, "");
    if (raw) {
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  const quoted = /\bfilename="([^"]+)"/i.exec(cd);
  if (quoted?.[1]) {
    const raw = quoted[1].trim();
    if (raw) {
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  const token = /\bfilename=([^;\s]+)/i.exec(cd);
  if (token?.[1]) {
    const raw = token[1].trim().replace(/^"|"$/g, "");
    if (raw) {
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  return fallback;
}
