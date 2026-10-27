let token = "";
export const setToken = (t: string) => { token = t; };

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const r = await fetch(`/api/v1${path}`, { ...init, headers });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    // The organisation now requires two-step sign-in and this person has not set it up: the app shows the enrolment screen.
    if (r.status === 403 && body.detail === "mfa_enrollment_required" && typeof window !== "undefined") window.dispatchEvent(new Event("mfa-needed"));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`);
  }
  return body as T;
}

/** Download a file from an authenticated endpoint (a plain link cannot send the Authorization header). */
export async function apiDownload(path: string, filename: string): Promise<void> {
  const r = await fetch(`/api/v1${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`);
  }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
