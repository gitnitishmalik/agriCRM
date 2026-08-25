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

export type User = components['schemas']['User']

const BASE = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, string[]> = {},
    readonly requestId: string | null = null,
  ) {
    super(message)
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
  },
  clear() {
    accessToken = null
    try {
      localStorage.removeItem(REFRESH_KEY)
    } catch {
      /* nothing to do */
    }
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

export async function api<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, headers, ...rest } = options

  const send = async (): Promise<Response> => {
    const h = new Headers(headers)
    if (body !== undefined) h.set('Content-Type', 'application/json')
    if (auth && accessToken) h.set('Authorization', `Bearer ${accessToken}`)

    return fetch(`${BASE}${path}`, {
      ...rest,
      headers: h,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  let res = await send()

  // One retry after a successful refresh. Never more — a refresh loop against
  // a revoked token is how you get rate-limited by your own client.
  if (res.status === 401 && auth && tokens.refresh) {
    if (await refreshAccessToken()) res = await send()
  }

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
