/**
 * Role display formatting.
 *
 * Split out from `layout/AppShell.tsx` so that file exports only components
 * and Fast Refresh keeps working. `Account.tsx` imported it from there, which
 * meant editing the shell remounted the account page.
 */

/** `data_ops` → `Data Ops`. The wire format is the backend's enum value. */
export function roleLabel(role: string): string {
  return role
    .split('_')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}
