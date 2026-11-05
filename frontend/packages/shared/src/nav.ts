/**
 * Back navigation (review-v3 §2.4). `window.history.length` counts the external payment page, so
 * we only go back when vue-router itself recorded a previous in-app entry (`history.state.back`);
 * otherwise (payment return, direct link, refresh) we `replace` to a fallback.
 */
export interface RouterLike {
  options: { history: { state: unknown } }
  back(): void
  replace(to: string): unknown
}

export function hasInAppBack(router: RouterLike): boolean {
  const st = router.options.history.state as { back?: unknown } | null | undefined
  return typeof st?.back === 'string' && st.back.length > 0
}

export function goBack(router: RouterLike, fallback: string): void {
  if (hasInAppBack(router)) router.back()
  else void router.replace(fallback)
}
