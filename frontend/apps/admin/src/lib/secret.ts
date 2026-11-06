/**
 * Write-only secret field state. The server never returns a secret, only `{configured, last4}`.
 * The input is never prefilled; the field is either untouched, replacing, or marked for clearing.
 */
export type SecretMode = 'idle' | 'replace' | 'clear'
export interface SecretField {
  configured: boolean
  last4: string | null
  mode: SecretMode
  value: string
}
export function secretFromServer(s?: { configured: boolean; last4?: string | null } | null): SecretField {
  return { configured: !!s?.configured, last4: s?.last4 ?? null, mode: 'idle', value: '' }
}
/** "Configured ••1234" label; falls back to bare dots when last4 is unknown. */
export function secretLabel(f: SecretField, configuredText: string): string {
  return f.last4 ? `${configuredText} ••${f.last4}` : `${configuredText} ••••`
}
/** Value to send to PUT /admin/site/secrets, or `undefined` when nothing should be sent (no change). */
export function secretChange(f: SecretField): { value: string | null } | undefined {
  if (f.mode === 'clear') return f.configured ? { value: null } : undefined
  if (f.mode === 'replace') {
    const v = f.value.trim()
    return v ? { value: v } : undefined
  }
  return undefined
}
