/**
 * API client.
 *
 * Types come from src/api/schema.d.ts, generated from the backend's OpenAPI
 * schema (`make schema-doc`). Doc 03 §4: generate the client types so a
 * backend field rename breaks the build rather than production.
 *
 * Token handling follows Doc 12 §1: a 15-minute access token and a 7-day
 * rotating refresh token. Access tokens live in memory only; the refresh
 * token goes to localStorage, which is a deliberate, bounded trade — see
 * the note on storage below.
 */

import type { components } from './schema'

export type User = components['schemas']['UserOut']

const BASE = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, string[]> = {},
    readonly requestId: string | null = null,
    // Carries the original failure when this wraps one — a `fetch` TypeError
    // from a request that never reached a server. Keeping it means the console
    // still shows what actually broke.
    options?: ErrorOptions,
  ) {
    super(message, options)
    this.name = 'ApiError'
  }

  /** Field-level messages for a form, from the Doc 11 §1 error envelope. */
  fieldErrors(): Record<string, string> {
    return Object.fromEntries(
      Object.entries(this.details).map(([k, v]) => [k, Array.isArray(v) ? v.join(' ') : String(v)]),
    )
  }
}

/**
 * Token storage.
 *
 * The access token is held in a module variable so it never reaches disk.
 * The refresh token is persisted, because a field agent on 2G being logged
 * out on every reload is a worse outcome than the storage risk — and the
 * server can revoke it (logout blacklists, password change revokes all).
 *
 * If this app ever serves an audience where XSS risk outweighs that, the
 * correct fix is an httpOnly cookie set by the backend, not sessionStorage.
 */
const REFRESH_KEY = 'agricrm.refresh'

let accessToken: string | null = null

/**
 * 🔴 Subscribers, so that holding a session is reactive state.
 *
 * Without this, `tokens` was a plain module object: `RequireAuth` read it
 * during render, and clearing it on sign-out changed nothing React could see.
 * The user stayed on the page they had just signed out of until something
 * else happened to re-render — a reload, usually — which looks exactly like
 * sign-out not working, and for a moment leaves a signed-out person looking
 * at data.
 *
 * `useSession()` in `auth.ts` reads this through `useSyncExternalStore`, so
 * the auth boundary now reacts to the token changing rather than depending on
 * a re-render arriving from somewhere else.
 */
const listeners = new Set<() => void>()

function notify() {
  for (const listener of listeners) listener()
}

export const tokens = {
  get access() {
    return accessToken
  },
  get refresh(): string | null {
    try {
      return localStorage.getItem(REFRESH_KEY)
    } catch {
      return null
    }
  },
  set(access: string, refresh?: string) {
    accessToken = access
    if (refresh) {
      try {
        localStorage.setItem(REFRESH_KEY, refresh)
      } catch {
        /* private mode — session-only login still works */
      }
    }
    notify()
  },
  clear() {
    accessToken = null
    try {
      localStorage.removeItem(REFRESH_KEY)
    } catch {
      /* nothing to do */
    }
    notify()
  },

  /** For `useSyncExternalStore`. Returns the unsubscribe. */
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },

  /**
   * A stable snapshot of "is there a session".
   *
   * A boolean rather than the token itself, deliberately: `useSyncExternalStore`
   * compares snapshots by identity and re-renders on every change, so returning
   * the token string would re-render the whole tree on each 15-minute refresh
   * for a fact that has not changed.
   */
  hasSession(): boolean {
    return Boolean(accessToken) || Boolean(tokens.refresh)
  },
}

/** Single-flight refresh: ten parallel 401s must not fire ten refreshes. */
let refreshInFlight: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async () => {
    const refresh = tokens.refresh
    if (!refresh) return false
    try {
      const res = await fetch(`${BASE}/api/v1/auth/refresh/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh }),
      })
      if (!res.ok) {
        tokens.clear()
        return false
      }
      const data = await res.json()
      tokens.set(data.access, data.refresh)
      return true
    } catch {
      return false
    } finally {
      refreshInFlight = null
    }
  })()

  return refreshInFlight
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  /** Set false for login/refresh, which must not recurse. */
  auth?: boolean
}

/**
 * Issue the request, retrying once behind a token refresh. Shared by `api`
 * (JSON) and `apiText` (HTML and other non-JSON documents) so both take the
 * same auth path — a second copy of the refresh logic is a second one to get
 * wrong.
 */
async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  const { body, auth = true, headers, ...rest } = options

  // A FormData body goes through untouched: the browser has to set its own
  // Content-Type so it can append the multipart boundary, and JSON.stringify
  // on a FormData yields "{}" — a silently empty upload.
  const isMultipart = body instanceof FormData

  const send = async (): Promise<Response> => {
    const h = new Headers(headers)
    if (body !== undefined && !isMultipart) h.set('Content-Type', 'application/json')
    if (auth && accessToken) h.set('Authorization', `Bearer ${accessToken}`)

    try {
      return await fetch(`${BASE}${path}`, {
        ...rest,
        headers: h,
        body: body === undefined ? undefined : isMultipart ? body : JSON.stringify(body),
      })
    } catch (cause) {
      // 🔴 `fetch` rejects with a bare TypeError when the request never
      // reaches a server — the API stopped, the dev proxy has nothing to
      // forward to, the machine is offline. Every screen here renders
      // `ApiError` and ignores anything else, so an unconverted TypeError is a
      // form that fails with no message at all next to it: the user retypes a
      // correct password and watches nothing happen.
      //
      // Status 0 because there was no response to take one from, and it must
      // not collide with a real 401 the sign-in screen words differently.
      throw new ApiError(
        0,
        'network_error',
        'Could not reach the API. If you are running this locally, start the ' +
          'backend with `python -m backend.run` from the repository root — and ' +
          'note that uvicorn started by hand listens on port 8000, while the ' +
          'dev server proxies to 8001.',
        {},
        null,
        { cause },
      )
    }
  }

  let res = await send()

  // One retry after a successful refresh. Never more — a refresh loop against
  // a revoked token is how you get rate-limited by your own client.
  if (res.status === 401 && auth && tokens.refresh) {
    if (await refreshAccessToken()) res = await send()
  }

  return res
}

export async function api<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const res = await request(path, options)

  if (res.status === 204 || res.status === 205) return undefined as T

  const payload = await res.json().catch(() => null)

  if (!res.ok) {
    const err = payload?.error ?? {}
    throw new ApiError(
      res.status,
      err.code ?? 'error',
      err.message ?? res.statusText,
      err.details ?? {},
      err.request_id ?? null,
    )
  }

  return payload as T
}

/**
 * 🔴 Fetch a document the browser cannot fetch for itself.
 *
 * `/invoices/{id}/html/` is behind the same bearer token as everything else,
 * and an `<iframe src>` or an `<a href>` is a plain browser GET with no
 * Authorization header on it. Pointing either at the endpoint renders the
 * API's 401 body inside the frame — which is what the invoice screen used to
 * show where the document should be. Fetch it here, where the token is, and
 * hand the caller the markup for `srcDoc` or a blob URL.
 */
export async function apiText(path: string, options: RequestOptions = {}): Promise<string> {
  const res = await request(path, options)
  const text = await res.text()

  if (!res.ok) {
    let err: Record<string, string> = {}
    try {
      err = JSON.parse(text)?.error ?? {}
    } catch {
      // A non-JSON error body (a proxy's HTML 502, say) — keep the status.
    }
    throw new ApiError(
      res.status,
      err.code ?? 'error',
      err.message ?? res.statusText,
      {},
      err.request_id ?? null,
    )
  }

  return text
}
