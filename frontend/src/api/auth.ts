/**
 * Auth queries and mutations (Doc 11 §2).
 *
 * TanStack Query owns server state. There is no client-side auth store beyond
 * the tokens themselves — the current user is a query like any other, so it
 * revalidates, caches and invalidates with the same rules as everything else.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, tokens, type User } from './client'

export interface LoginResponse {
  access: string
  refresh: string
  user: User
  mfa_required: boolean
  mfa_enrolled: boolean
}

export const meKey = ['auth', 'me'] as const

export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: () => api<User>('/api/v1/auth/me/'),
    enabled: Boolean(tokens.access || tokens.refresh),
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (creds: { email: string; password: string }) =>
      api<LoginResponse>('/api/v1/auth/login/', {
        method: 'POST',
        body: creds,
        auth: false,
      }),
    onSuccess: (data) => {
      tokens.set(data.access, data.refresh)
      qc.setQueryData(meKey, data.user)
    },
  })
}

export function useVerifyMfa() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) =>
      api<{ access: string; refresh: string }>('/api/v1/auth/mfa/verify/', {
        method: 'POST',
        body: { token: code },
      }),
    onSuccess: (data) => {
      tokens.set(data.access, data.refresh)
      qc.invalidateQueries({ queryKey: meKey })
    },
  })
}

export interface MfaEnrolResponse {
  provisioning_uri: string
  /** Inline SVG. Rendered rather than shown as text — nobody types a URI. */
  qr_svg: string
  /** Base32, for entering by hand when there is no camera. */
  secret: string
  already_confirmed: boolean
}

export function useEnrolMfa() {
  return useMutation({
    mutationFn: () =>
      api<MfaEnrolResponse>('/api/v1/auth/mfa/enrol/', { method: 'POST' }),
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const refresh = tokens.refresh
      // Best effort: if the network is down the local session still ends.
      if (refresh) {
        await api('/api/v1/auth/logout/', { method: 'POST', body: { refresh } }).catch(() => {})
      }
    },
    onSettled: () => {
      tokens.clear()
      qc.clear()
    },
  })
}
