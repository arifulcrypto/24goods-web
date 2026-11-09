/**
 * Site-wide visit gate (review-v9 §7). When the visit challenge is on, EVERY storefront route needs
 * clearance: there is no route allow-list. Only a 428 raised mid-session (the visitor was cleared
 * a moment ago) degrades to a docked banner so typed text and open sheets are not thrown away.
 */
export type GateView = 'gate' | 'banner' | 'open'

export function gateView(s: { blocked: boolean; midSession: boolean }): GateView {
  if (!s.blocked) return 'open'
  return s.midSession ? 'banner' : 'gate'
}
