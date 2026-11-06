import { useAuthStore } from '../stores/auth'
import { usePow } from './usePow'

/**
 * Make sure there is a signed-in user: when there is none, create an instant account through the
 * existing PoW flow (review-v4 §5). Credentials land in `auth.guestCreds` (memory) for the
 * "Save login" banner / sheet.
 */
export function useEnsureAccount() {
  const auth = useAuthStore()
  const pow = usePow()
  async function ensure(): Promise<void> {
    await auth.init()
    if (auth.loggedIn) return
    const solution = await pow.run('instant')
    await auth.instant(solution)
  }
  return { ensure, solving: pow.solving, progress: pow.progress, cancel: pow.cancel }
}
