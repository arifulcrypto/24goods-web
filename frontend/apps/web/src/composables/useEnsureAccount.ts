import { useAuthStore } from '../stores/auth'
import { usePow } from './usePow'
import { useTurnstile } from './useTurnstile'

/**
 * Make sure there is a signed-in user: when there is none, create an instant account through the
 * existing PoW flow (review-v4 §5) plus Turnstile when the site protects `instant_account` (v5 §5.1).
 * The page must render `<TurnstileWidget>` while `tsNeeded && !auth.loggedIn` (see ChatPage / CartPage).
 * Credentials land in `auth.guestCreds` (memory) for the "Save login" banner / sheet.
 */
export function useEnsureAccount() {
  const auth = useAuthStore()
  const pow = usePow()
  const ts = useTurnstile('instant_account')
  async function ensure(): Promise<void> {
    await auth.init()
    if (auth.loggedIn) return
    const token = ts.require()
    const solution = await pow.run('instant')
    try {
      await auth.instant(solution, token)
    } finally {
      ts.reset()
    }
  }
  return {
    ensure,
    solving: pow.solving,
    progress: pow.progress,
    cancel: pow.cancel,
    ts,
    tsNeeded: ts.needed,
  }
}
