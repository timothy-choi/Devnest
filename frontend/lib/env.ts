const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const DEFAULT_APP_BASE_URL = "http://localhost:3000";

export function getApiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL;
}

/**
 * Base URL for Next.js **server** calls to the FastAPI backend (API routes, `getServerSideProps`).
 * Browsers use {@link getApiBaseUrl} via `NEXT_PUBLIC_API_BASE_URL` (e.g. host `localhost:8000`).
 * Inside Docker, `localhost` in the frontend container is not the API; set `INTERNAL_API_BASE_URL`
 * to the compose service (e.g. `http://backend:8000`).
 */
export function getServerBackendBaseUrl() {
  const fromEnv = (process.env.INTERNAL_API_BASE_URL || process.env.API_BASE_URL || "").trim();
  if (fromEnv) {
    return fromEnv.replace(/\/$/, "");
  }
  return getApiBaseUrl();
}

export function getAppBaseUrl() {
  return process.env.NEXT_PUBLIC_APP_BASE_URL || DEFAULT_APP_BASE_URL;
}

export function getAppOrigin() {
  try {
    return new URL(getAppBaseUrl()).origin;
  } catch {
    return null;
  }
}
